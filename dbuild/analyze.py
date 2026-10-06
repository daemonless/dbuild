"""Image layer and filesystem bloat analysis.

Inspects container image layers for size distribution and zombie layers
(cleanup instructions in separate layers that fail to reclaim space),
and audits the in-container rootfs for leftover build toolchains, static
libraries, development headers, and package caches.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from dbuild import log, podman
from dbuild.config import Config


def human_size(num_bytes: int) -> str:
    """Format bytes as a human-readable string (e.g. 14.2 MB, 1.3 GB)."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    num = float(num_bytes)
    for unit in ("KB", "MB", "GB", "TB"):
        num /= 1024.0
        if num < 1024.0 or unit == "TB":
            return f"{num:.1f} {unit}"
    return f"{num:.1f} PB"


# Regex patterns matching build-only tools that shouldn't linger in production
_BUILD_TOOL_PATTERNS = [
    re.compile(r"^devel/llvm\d*", re.IGNORECASE),
    re.compile(r"^devel/gcc\d*", re.IGNORECASE),
    re.compile(r"^devel/binutils", re.IGNORECASE),
    re.compile(r"^lang/rust", re.IGNORECASE),
    re.compile(r"^lang/go\d*", re.IGNORECASE),
    re.compile(r"^devel/cmake", re.IGNORECASE),
    re.compile(r"^devel/ninja", re.IGNORECASE),
    re.compile(r"^devel/meson", re.IGNORECASE),
    re.compile(r"^devel/gmake", re.IGNORECASE),
    re.compile(r"^devel/bison", re.IGNORECASE),
    re.compile(r"^devel/flex", re.IGNORECASE),
    re.compile(r"^devel/m4", re.IGNORECASE),
    re.compile(r"^devel/git-tiny", re.IGNORECASE),
    re.compile(r"^devel/git", re.IGNORECASE),
]

_STANDALONE_CLEANUP_RE = re.compile(
    r"(?:/bin/sh -c\s+)?(?:rm\s+-[a-zA-Z]*r[a-zA-Z]*f|pkg\s+clean)",
    re.IGNORECASE,
)


@dataclass
class LayerInfo:
    id: str
    size_bytes: int
    size_human: str
    created_by: str
    comment: str = ""
    is_nop: bool = False


@dataclass
class BloatItem:
    category: str
    title: str
    size_bytes: int
    size_human: str
    details: list[str] = field(default_factory=list)
    recommendation: str = ""


@dataclass
class AnalysisReport:
    image: str
    total_size_bytes: int
    total_size_human: str
    layers: list[LayerInfo] = field(default_factory=list)
    bloat_items: list[BloatItem] = field(default_factory=list)
    zombie_layers: list[dict[str, Any]] = field(default_factory=list)
    potential_savings_bytes: int = 0
    potential_savings_human: str = "0 B"
    rootfs_audit_available: bool = True
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_history_json(raw_history: list[dict[str, Any]]) -> list[LayerInfo]:
    """Parse output of ``podman history --format json`` into LayerInfo models."""
    layers: list[LayerInfo] = []
    for item in raw_history:
        layer_id = str(item.get("id") or item.get("Id") or "<missing>")
        raw_size = item.get("size")
        if raw_size is None:
            raw_size = item.get("Size", 0)
        size_bytes = int(raw_size)
        created_by = str(item.get("CreatedBy") or item.get("created_by") or "")
        comment = str(item.get("comment") or item.get("Comment") or "")
        is_nop = "#(nop)" in created_by or "# (nop)" in created_by

        layers.append(
            LayerInfo(
                id=layer_id,
                size_bytes=size_bytes,
                size_human=human_size(size_bytes),
                created_by=created_by.strip(),
                comment=comment.strip(),
                is_nop=is_nop,
            )
        )
    return layers


def detect_zombie_layers(layers: list[LayerInfo]) -> list[dict[str, Any]]:
    """Detect separate cleanup RUN layers that cannot reclaim disk space."""
    zombies: list[dict[str, Any]] = []
    for idx, layer in enumerate(layers):
        cmd = layer.created_by
        # Check for standalone cleanup commands like `RUN rm -rf /var/cache/pkg`
        if _STANDALONE_CLEANUP_RE.search(cmd) and "pkg install" not in cmd and "make" not in cmd and "fetch" not in cmd and "curl" not in cmd:
            zombies.append({
                    "layer_index": idx,
                    "layer_id": layer.id[:12] if len(layer.id) >= 12 else layer.id,
                    "command": cmd,
                    "reason": (
                        "Separate cleanup layer does not reclaim disk space from parent "
                        "layers due to OCI copy-on-write semantics. Combine file deletion "
                        "into the same RUN instruction using '&&'."
                    ),
                })
    return zombies


# In-container probe script using only standard FreeBSD base utilities (/bin/sh, du, find, pkg)
_PROBE_SCRIPT = (
    "echo '===PKGS===' && "
    "pkg query '%o\t%n\t%v\t%sb\t%a' 2>/dev/null || true; "
    "echo '===STATIC_LIBS===' && "
    "find /usr/local /usr/lib -name '*.a' -exec du -k {} + 2>/dev/null || true; "
    "echo '===DIRECTORIES===' && "
    "du -sk /usr/local/include /usr/local/share/doc /usr/local/share/man "
    "/var/cache/pkg /var/db/pkg/repos /tmp /var/tmp 2>/dev/null || true; "
    "echo '===REPO_SQLITE===' && "
    "du -sk /var/db/pkg/repo-*.sqlite 2>/dev/null || true; "
    "echo '===END==='"
)


def parse_probe_output(output: str, threshold_bytes: int = 1024 * 1024) -> list[BloatItem]:
    """Parse raw probe output into categorized BloatItems."""
    bloat: list[BloatItem] = []

    sections: dict[str, list[str]] = {}
    current_sec = "NONE"
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("===") and line.endswith("==="):
            current_sec = line.strip("=")
            sections[current_sec] = []
        elif current_sec != "NONE":
            sections[current_sec].append(line)

    # 1. Build Toolchain & Compilers
    pkg_lines = sections.get("PKGS", [])
    build_pkgs: list[tuple[str, str, int]] = []
    build_bytes = 0

    for pl in pkg_lines:
        parts = pl.split("\t")
        if len(parts) >= 4:
            origin, name, _ver, size_str = parts[0], parts[1], parts[2], parts[3]
            try:
                pkg_size = int(size_str)
            except ValueError:
                continue

            for pat in _BUILD_TOOL_PATTERNS:
                if pat.search(origin) or pat.search(name):
                    build_pkgs.append((origin, name, pkg_size))
                    build_bytes += pkg_size
                    break

    if build_pkgs and build_bytes >= threshold_bytes:
        details = [
            f"{orig} ({name}): {human_size(sz)}"
            for orig, name, sz in sorted(build_pkgs, key=lambda x: x[2], reverse=True)[:10]
        ]
        bloat.append(
            BloatItem(
                category="toolchain",
                title=f"Build Toolchains & Compilers ({len(build_pkgs)} packages)",
                size_bytes=build_bytes,
                size_human=human_size(build_bytes),
                details=details,
                recommendation=(
                    "Use a multi-stage build (e.g. 'AS builder') to compile artifacts, "
                    "or purge build tools in the same layer before finalizing: "
                    f"'pkg delete -y {' '.join(p[1] for p in build_pkgs[:5])}'"
                ),
            )
        )

    # 2. Static Libraries (*.a)
    static_lines = sections.get("STATIC_LIBS", [])
    static_files: list[tuple[str, int]] = []
    static_bytes = 0

    for sl in static_lines:
        parts = sl.split("\t", 1)
        if len(parts) == 2:
            try:
                kb = int(parts[0])
                static_bytes += kb * 1024
                static_files.append((parts[1], kb * 1024))
            except ValueError:
                continue

    if static_files and static_bytes >= threshold_bytes:
        details = [
            f"{path}: {human_size(sz)}"
            for path, sz in sorted(static_files, key=lambda x: x[1], reverse=True)[:8]
        ]
        if len(static_files) > 8:
            details.append(f"... and {len(static_files) - 8} more static archives")
        bloat.append(
            BloatItem(
                category="static_libs",
                title=f"Static Libraries ({len(static_files)} files)",
                size_bytes=static_bytes,
                size_human=human_size(static_bytes),
                details=details,
                recommendation="Add 'find /usr/local -name \"*.a\" -delete' to your installation RUN layer.",
            )
        )

    # 3. Directories (Headers, Doc, Man, Cache)
    dir_lines = sections.get("DIRECTORIES", [])
    dir_sizes: dict[str, int] = {}
    for dl in dir_lines:
        parts = dl.split("\t", 1)
        if len(parts) == 2:
            try:
                dir_sizes[parts[1]] = int(parts[0]) * 1024
            except ValueError:
                continue

    # Development Headers
    inc_bytes = dir_sizes.get("/usr/local/include", 0)
    if inc_bytes >= threshold_bytes:
        bloat.append(
            BloatItem(
                category="headers",
                title="Development Headers (/usr/local/include)",
                size_bytes=inc_bytes,
                size_human=human_size(inc_bytes),
                details=[f"/usr/local/include: {human_size(inc_bytes)}"],
                recommendation="Remove C/C++ header files with 'rm -rf /usr/local/include' in the install layer.",
            )
        )

    # Documentation & Man Pages
    doc_bytes = dir_sizes.get("/usr/local/share/doc", 0) + dir_sizes.get("/usr/local/share/man", 0)
    if doc_bytes >= threshold_bytes:
        details = []
        if dir_sizes.get("/usr/local/share/doc"):
            details.append(f"/usr/local/share/doc: {human_size(dir_sizes['usr/local/share/doc'] if 'usr/local/share/doc' in dir_sizes else dir_sizes.get('/usr/local/share/doc', 0))}")
        if dir_sizes.get("/usr/local/share/man"):
            details.append(f"/usr/local/share/man: {human_size(dir_sizes.get('/usr/local/share/man', 0))}")
        bloat.append(
            BloatItem(
                category="docs",
                title="Manual & Documentation Pages",
                size_bytes=doc_bytes,
                size_human=human_size(doc_bytes),
                details=details,
                recommendation="Remove documentation with 'rm -rf /usr/local/share/doc /usr/local/share/man'.",
            )
        )

    # Package Caches & Repo Catalogs
    cache_bytes = dir_sizes.get("/var/cache/pkg", 0) + dir_sizes.get("/var/db/pkg/repos", 0)
    # Repo sqlite files
    for rline in sections.get("REPO_SQLITE", []):
        rparts = rline.split("\t", 1)
        if len(rparts) == 2:
            try:
                cache_bytes += int(rparts[0]) * 1024
            except ValueError:
                continue

    # Flag cache if > 64 KB
    if cache_bytes > 64 * 1024:
        bloat.append(
            BloatItem(
                category="cache",
                title="Package Cache & Catalogs (/var/cache/pkg)",
                size_bytes=cache_bytes,
                size_human=human_size(cache_bytes),
                details=[f"Leftover pkg cache/catalogs: {human_size(cache_bytes)}"],
                recommendation="Ensure your RUN layer ends with 'pkg clean -ay && rm -rf /var/cache/pkg/* /var/db/pkg/repos/* /var/db/pkg/repo-*.sqlite'.",
            )
        )

    return bloat


def analyze_image(
    image: str,
    threshold_mb: float = 5.0,
    *,
    skip_container_audit: bool = False,
    quiet: bool = False,
) -> AnalysisReport:
    """Analyze an image's layer history and in-container filesystem bloat."""
    threshold_bytes = int(threshold_mb * 1024 * 1024)

    # 1. Layer History
    try:
        raw_history = podman.history(image, quiet=quiet)
    except Exception as e:
        return AnalysisReport(
            image=image,
            total_size_bytes=0,
            total_size_human="0 B",
            error_message=f"Failed to inspect image history: {e}",
            rootfs_audit_available=False,
        )

    if not raw_history:
        return AnalysisReport(
            image=image,
            total_size_bytes=0,
            total_size_human="0 B",
            error_message=f"Image {image!r} not found or has empty history",
            rootfs_audit_available=False,
        )

    layers = parse_history_json(raw_history)
    total_size = sum(layer.size_bytes for layer in layers)
    zombies = detect_zombie_layers(layers)

    # 2. In-Container Rootfs Probe
    bloat_items: list[BloatItem] = []
    audit_available = True

    if not skip_container_audit:
        try:
            probe_out = podman.run_in(image, _PROBE_SCRIPT, quiet=quiet)
            bloat_items = parse_probe_output(probe_out, threshold_bytes=threshold_bytes)
        except Exception as e:
            if not quiet:
                log.warn(f"Could not execute in-container audit on {image}: {e}")
            audit_available = False

    potential_savings = sum(b.size_bytes for b in bloat_items)

    return AnalysisReport(
        image=image,
        total_size_bytes=total_size,
        total_size_human=human_size(total_size),
        layers=layers,
        bloat_items=bloat_items,
        zombie_layers=zombies,
        potential_savings_bytes=potential_savings,
        potential_savings_human=human_size(potential_savings),
        rootfs_audit_available=audit_available,
    )


def print_report(report: AnalysisReport, threshold_mb: float = 5.0) -> None:
    """Print an analysis report to terminal."""
    if report.error_message:
        log.error(report.error_message)
        return

    print()
    log.step(f"Image Analysis: {report.image}")
    log.info(f"Total Image Size: {report.total_size_human} ({report.total_size_bytes:,} bytes)")
    log.info(f"Total Layers:     {len(report.layers)}")

    # Zombie layers
    if report.zombie_layers:
        print()
        log.warn(f"Detected {len(report.zombie_layers)} Zombie / Dead Cleanup Layer(s):")
        for z in report.zombie_layers:
            log.warn(f"  • Layer [{z['layer_id']}]: {z['command']}")
            log.info(f"    ↳ {z['reason']}")

    # Top largest layers
    sorted_layers = sorted(report.layers, key=lambda layer: layer.size_bytes, reverse=True)
    large_layers = [layer for layer in sorted_layers if layer.size_bytes >= threshold_mb * 1024 * 1024]

    print()
    log.step(f"Largest Layers (>= {threshold_mb} MB)")
    if not large_layers:
        log.info(f"  All layers are under {threshold_mb} MB.")
    else:
        for idx, layer in enumerate(large_layers[:6], 1):
            pct = (layer.size_bytes / report.total_size_bytes * 100) if report.total_size_bytes > 0 else 0
            layer_id = layer.id[:12] if len(layer.id) >= 12 else layer.id
            cmd = layer.created_by
            if len(cmd) > 80:
                cmd = cmd[:77] + "..."
            log.info(f"  {idx}. {layer.size_human:>9} ({pct:4.1f}%) [{layer_id}] {cmd}")

    # Rootfs bloat findings
    print()
    log.step("Rootfs Bloat & Artifact Audit")
    if not report.rootfs_audit_available:
        log.warn("  (In-container audit skipped or unavailable; layer analysis only)")
    elif not report.bloat_items:
        log.success("  No toolchains, static libraries, or uncleaned caches detected!")
    else:
        for b in report.bloat_items:
            pct = (b.size_bytes / report.total_size_bytes * 100) if report.total_size_bytes > 0 else 0
            log.warn(f"  • {b.title} — {b.size_human} ({pct:.1f}% of image)")
            for d in b.details:
                log.info(f"      - {d}")
            if b.recommendation:
                log.success(f"      Recommendation: {b.recommendation}")

        print()
        log.step("Optimization Summary")
        savings_pct = (
            (report.potential_savings_bytes / report.total_size_bytes * 100)
            if report.total_size_bytes > 0
            else 0
        )
        log.success(
            f"Potential Recoverable Space: {report.potential_savings_human} ({savings_pct:.1f}% reduction)"
        )
    print()


def run_analyze(cfg: Config, args: argparse.Namespace) -> int:
    """CLI entrypoint for 'dbuild analyze'."""
    image = getattr(args, "image", None)
    img_name = getattr(cfg, "image", None) or getattr(cfg, "name", None) or "image"

    if not image:
        # Default to the first detected/configured variant
        variants = getattr(cfg, "variants", [])
        variant_filter = getattr(args, "variant", None)
        if variant_filter:
            want = set(variant_filter.split(","))
            variants = [v for v in variants if v.tag in want]

        if variants:
            image = f"localhost/{img_name}:{variants[0].tag}"
            if not podman.image_exists(image):
                # Try build-{tag} staging name
                build_image = f"localhost/{img_name}:build-{variants[0].tag}"
                if podman.image_exists(build_image):
                    image = build_image
        else:
            image = f"localhost/{img_name}:latest"
            if not podman.image_exists(image):
                build_image = f"localhost/{img_name}:build-latest"
                if podman.image_exists(build_image):
                    image = build_image
                else:
                    log.error(
                        f"Image {image!r} not found in local storage. "
                        "Specify an image to analyze (e.g. 'dbuild analyze <image>'), "
                        "or run 'dbuild build' first."
                    )
                    return 1

    threshold_mb = getattr(args, "threshold_mb", 5.0)
    skip_container = getattr(args, "skip_container", False)
    is_json = bool(getattr(args, "json_output", False))
    report = analyze_image(
        image,
        threshold_mb=threshold_mb,
        skip_container_audit=skip_container,
        quiet=is_json,
    )

    if is_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print_report(report, threshold_mb=threshold_mb)

    return 1 if report.error_message else 0
