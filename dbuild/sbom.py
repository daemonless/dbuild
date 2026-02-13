"""SBOM (Software Bill of Materials) generation.

For each variant (optionally filtered by ``--variant``):

1. Mounts the image filesystem via buildah.
2. Runs a Trivy rootfs scan for application-level dependencies.
3. Extracts FreeBSD packages via ``pkg query`` inside the container.
4. Builds a SBOM JSON document (matching the structure produced by the
   legacy ``generate-sbom.sh`` script).
5. Writes the result to ``sbom-results/``.

This module does NOT build, push, or test.
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
from pathlib import Path
from typing import Any

from dbuild import log, podman
from dbuild.config import Config, Variant

# Package type categories extracted from Trivy output.
_TRIVY_PKG_TYPES: dict[str, list[str]] = {
    "dotnet": ["dotnet-core"],
    "go": ["gobinary", "gomod"],
    "java": ["jar", "pom"],
    "node": ["node-pkg"],
    "php": ["composer"],
    "python": ["python-pkg"],
    "ruby": ["bundler", "gemspec"],
    "rust": ["rustbinary", "cargo"],
}


def _detect_source(variant: Variant) -> str:
    """Derive the source type from the variant's containerfile.

    If the containerfile has a suffix (e.g. ``Containerfile.pkg``),
    the suffix is used.  Otherwise ``"upstream"`` is returned.
    """
    cf = variant.containerfile
    if "." in cf:
        return cf.split(".", 1)[1]
    return "upstream"


def _run_trivy(mount_path: str) -> dict[str, Any]:
    """Run ``trivy rootfs`` against *mount_path* and return parsed JSON.

    Returns an empty dict on failure.
    """
    log.info("Running Trivy scan...")
    cmd = [
        *podman._priv_prefix(),
        "trivy", "rootfs", mount_path,
        "--format", "json",
        "--scanners", "vuln",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        log.warn(f"Trivy scan returned exit code {result.returncode}")
        if result.stderr:
            log.warn(result.stderr.strip())
    try:
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError:
        log.warn("Could not parse Trivy JSON output")
        return {}


def _extract_trivy_packages(trivy_data: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """Extract per-type package lists from Trivy output."""
    packages: dict[str, list[dict[str, str]]] = {
        category: [] for category in _TRIVY_PKG_TYPES
    }

    results = trivy_data.get("Results", [])
    for result in results:
        result_type = result.get("Type", "")
        for category, type_names in _TRIVY_PKG_TYPES.items():
            if result_type in type_names:
                for pkg in result.get("Packages", []):
                    entry = {
                        "name": pkg.get("Name", ""),
                        "version": pkg.get("Version", ""),
                    }
                    # De-duplicate by name within category.
                    if not any(p["name"] == entry["name"] for p in packages[category]):
                        packages[category].append(entry)

    return packages


def _extract_freebsd_packages(image_ref: str) -> list[dict[str, str]]:
    """Extract installed FreeBSD packages via ``pkg query`` inside the image."""
    log.info("Extracting FreeBSD packages...")
    try:
        output = podman.run_in(image_ref, ["pkg", "query", "%n %v"])
    except podman.PodmanError:
        log.warn("Could not query FreeBSD packages")
        return []

    # Strip control characters (e.g. STX \x02) that pkg query can emit.
    output = ''.join(c for c in output if c.isprintable() or c == '\n')

    packages: list[dict[str, str]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            packages.append({"name": parts[0], "version": parts[1]})
    return packages


def _extract_app_version(image_ref: str) -> str:
    """Get the application version from inside the container.

    Falls back to ``pkg query`` for the title package, then ``"unknown"``.
    """
    try:
        ver = podman.run_in(image_ref, "cat /app/version 2>/dev/null || "
                            "pkg query \"%v\" $(pkg query -e \"%At = title\" \"%n\") "
                            "2>/dev/null | head -1 || echo unknown")
        return ver if ver else "unknown"
    except podman.PodmanError:
        return "unknown"


def _generate_sbom(
    cfg: Config,
    variant: Variant,
    arch: str,
) -> dict[str, Any]:
    """Generate the SBOM JSON for one variant."""
    build_ref = f"{cfg.full_image}:build-{variant.tag}"
    source = _detect_source(variant)

    log.step(f"Generating SBOM for :{variant.tag}")
    log.info(f"Image: {build_ref}")
    log.info(f"Source: {source}")

    # Get app version.
    app_version = _extract_app_version(build_ref)
    log.info(f"App version: {app_version}")

    # Mount via buildah for Trivy rootfs scan.
    log.info("Mounting image filesystem...")
    container_id = podman.bah_from(build_ref)
    try:
        mount_path = podman.bah_mount(container_id)
        trivy_data = _run_trivy(mount_path)
        podman.bah_umount(container_id)
    finally:
        podman.bah_rm(container_id)

    # Extract packages.
    trivy_packages = _extract_trivy_packages(trivy_data)
    freebsd_packages = _extract_freebsd_packages(build_ref)

    # Build the SBOM document.
    generated = datetime.datetime.now(tz=datetime.UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    summary: dict[str, int] = {
        "freebsd": len(freebsd_packages),
    }
    total = len(freebsd_packages)
    for category, pkgs in trivy_packages.items():
        summary[category] = len(pkgs)
        total += len(pkgs)
    summary["total"] = total

    # Include arch suffix in tag for non-amd64 so the merge step
    # produces separate entries (e.g. "15" and "15-aarch64").
    arch_suffix = f"-{arch}" if arch != "amd64" else ""
    sbom_tag = f"{variant.tag}{arch_suffix}"

    sbom: dict[str, Any] = {
        "image": cfg.image,
        "tag": sbom_tag,
        "arch": arch,
        "app_version": app_version,
        "source": source,
        "generated": generated,
        "packages": {
            "freebsd": freebsd_packages,
            **trivy_packages,
        },
        "summary": summary,
    }

    return sbom


def run(cfg: Config, args: argparse.Namespace) -> None:
    """Generate SBOMs for all (or filtered) variants.

    Parameters
    ----------
    cfg:
        Parsed build configuration.
    args:
        CLI arguments.  Recognised attributes:

        * ``variant``    -- generate only for this tag (optional).
        * ``arch``       -- target architecture (optional, defaults to first).
        * ``output_dir`` -- output directory (optional, defaults to ``sbom-results``).
    """
    from dbuild import ci as ci_mod
    backend = ci_mod.detect()
    if backend.should_skip("sbom"):
        log.info("Skipping SBOM generation ([skip sbom] in commit message)")
        return

    variant_filter: str | None = getattr(args, "variant", None)
    arch: str = getattr(args, "arch", None) or cfg.architectures[0]
    output_dir = Path(getattr(args, "output_dir", None) or "sbom-results")
    output_dir.mkdir(parents=True, exist_ok=True)

    generated: list[str] = []

    for variant in cfg.variants:
        if variant_filter and variant.tag != variant_filter:
            continue

        sbom = _generate_sbom(cfg, variant, arch)
        sbom_file = output_dir / f"{cfg.image}-{variant.tag}-sbom.json"

        with open(sbom_file, "w") as fh:
            json.dump(sbom, fh, indent=2)
            fh.write("\n")

        log.step("SBOM Complete")
        log.info(f"Summary: {json.dumps(sbom['summary'])}")
        log.success(f"Output: {sbom_file}")
        generated.append(str(sbom_file))

    if not generated:
        log.warn("No variants matched the filter")
        return

    log.step("SBOM generation summary")
    for path in generated:
        log.success(f"  {path}")
