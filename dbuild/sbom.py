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

from dbuild import VERSION, log, podman
from dbuild.config import Config, Variant, arch_tag_suffix

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
                        # Carry Trivy's own purl through (pkg:npm/..., pkg:golang/...)
                        # so Mode B exporters don't have to reconstruct it.
                        "purl": pkg.get("PkgIdentifier", {}).get("PURL", ""),
                    }
                    # De-duplicate by name within category.
                    if not any(p["name"] == entry["name"] for p in packages[category]):
                        packages[category].append(entry)

    return packages


# name<TAB>version<TAB>licenses. Bare %L is what actually works on a live
# image (verified on saturn: `pkg query "%n\t%v\t%L" zstd` ->
# `zstd\t1.5.7_2\tGPLv2, BSD3CLAUSE`). The %L%{%Ln%|,%} iteration form pkg
# *rejects* (non-zero exit), which previously zeroed the whole inventory.
# \t stays a literal backslash-t so pkg (not Python) expands it, matching the
# verified command exactly.
_PKG_QUERY_FMT = r"%n\t%v\t%L"


def _parse_freebsd_query(output: str) -> list[dict[str, Any]]:
    """Parse ``name<TAB>version<TAB>licenses`` pkg query output.

    The license field is pkg-formatted (comma-space separated, e.g.
    ``GPLv2, BSD3CLAUSE``) and may be empty. Licenses are FreeBSD's own
    codes, not SPDX ids; they are included only when present.
    """
    # Keep tab/newline separators; strip other control chars (e.g. STX \x02).
    output = ''.join(c for c in output if c.isprintable() or c in '\n\t')

    packages: list[dict[str, Any]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        entry: dict[str, Any] = {"name": parts[0], "version": parts[1]}
        if len(parts) > 2:
            licenses = [lic.strip() for lic in parts[2].split(",") if lic.strip()]
            if licenses:
                entry["licenses"] = licenses
        packages.append(entry)
    return packages


def _extract_freebsd_packages(image_ref: str) -> list[dict[str, Any]]:
    """Extract installed FreeBSD packages (name, version, licenses)."""
    log.info("Extracting FreeBSD packages...")
    try:
        output = podman.run_in(image_ref, ["pkg", "query", _PKG_QUERY_FMT])
    except podman.PodmanError:
        log.warn("Could not query FreeBSD packages")
        return []
    return _parse_freebsd_query(output)


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
    sbom_tag = f"{variant.tag}{arch_tag_suffix(arch)}"

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


def _freebsd_purl(name: str, version: str, arch: str) -> str:
    """Build a spec-legal purl for a FreeBSD package.

    Uses ``pkg:generic`` — there is no registered purl type for FreeBSD.
    This parses cleanly in any scanner but does NOT get CVE-feed matching:
    no vulnerability database is keyed to FreeBSD packages under any type.
    See sbom-c.md findings; the caveat is surfaced in the document metadata.
    """
    # TODO: derive the major from `pkg config ABI` when extraction is next
    # touched; hardcoded here because a generic purl won't CVE-match regardless.
    return (
        f"pkg:generic/{name}@{version}"
        f"?os=freebsd&distro=freebsd-15&arch={arch}"
    )


def _generate_cyclonedx(sbom_data: dict[str, Any]) -> dict[str, Any]:
    """Transform the internal SBOM dict into a CycloneDX 1.6 JSON document.

    Pure serializer: reads only what ``_generate_sbom`` already collected.
    Trivy-derived components carry Trivy's real purl (Phase 1); FreeBSD
    packages get a ``pkg:generic`` purl that parses but does not CVE-match.
    """
    arch = sbom_data.get("arch", "")
    pkgs = sbom_data.get("packages", {})

    components: list[dict[str, Any]] = []
    seen_refs: set[str] = set()

    def _add(name: str, version: str, purl: str,
             licenses: list[str] | None = None) -> None:
        if not name:
            return
        comp: dict[str, Any] = {"type": "library", "name": name}
        if version:
            comp["version"] = version
        if purl:
            comp["purl"] = purl
        if licenses:
            # CycloneDX allows free-text license names -- correct for FreeBSD
            # codes, which are not SPDX license ids.
            comp["licenses"] = [{"license": {"name": lic}} for lic in licenses]
        # bom-ref must be unique across the document.
        ref = purl or f"{name}@{version}"
        if ref in seen_refs:
            suffix = 2
            while f"{ref}#{suffix}" in seen_refs:
                suffix += 1
            ref = f"{ref}#{suffix}"
        seen_refs.add(ref)
        comp["bom-ref"] = ref
        components.append(comp)

    # FreeBSD packages first, then application-layer (Trivy) packages.
    for pkg in pkgs.get("freebsd", []):
        name, version = pkg.get("name", ""), pkg.get("version", "")
        _add(name, version, _freebsd_purl(name, version, arch),
             pkg.get("licenses"))

    for category, entries in pkgs.items():
        if category == "freebsd":
            continue
        for pkg in entries:
            _add(pkg.get("name", ""), pkg.get("version", ""), pkg.get("purl", ""))

    image = sbom_data.get("image", "")
    tag = sbom_data.get("tag", "")
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "timestamp": sbom_data.get("generated", ""),
            "tools": {
                "components": [
                    {"type": "application", "name": "dbuild", "version": VERSION},
                ]
            },
            "component": {
                "type": "container",
                "bom-ref": f"{image}:{tag}",
                "name": image,
                "version": sbom_data.get("app_version", "") or "unknown",
            },
            "properties": [
                {
                    "name": "dbuild:freebsd-cve-caveat",
                    "value": _FREEBSD_CVE_CAVEAT,
                },
                {"name": "dbuild:source", "value": sbom_data.get("source", "")},
                {"name": "dbuild:arch", "value": arch},
            ],
        },
        "components": components,
    }


_FREEBSD_CVE_CAVEAT = (
    "pkg:generic FreeBSD components parse but have no FreeBSD advisory feed "
    "in any scanner; they will not auto-match CVEs."
)


def _generate_spdx(sbom_data: dict[str, Any]) -> dict[str, Any]:
    """Transform the internal SBOM dict into an SPDX 2.3 JSON document.

    Pure serializer over the same collected data as ``_generate_cyclonedx``.
    Purls ride in each package's ``externalRefs`` (referenceType ``purl``);
    the FreeBSD ``pkg:generic`` caveat is stated in the document ``comment``.
    """
    arch = sbom_data.get("arch", "")
    pkgs = sbom_data.get("packages", {})
    image = sbom_data.get("image", "")
    tag = sbom_data.get("tag", "")
    generated = sbom_data.get("generated", "")

    packages: list[dict[str, Any]] = []
    relationships: list[dict[str, str]] = []

    # Root package = the image itself; the document DESCRIBES it.
    root_id = "SPDXRef-Package-image"
    packages.append({
        "SPDXID": root_id,
        "name": image,
        "versionInfo": sbom_data.get("app_version", "") or "NOASSERTION",
        "downloadLocation": "NOASSERTION",
        # We inventory packages, not their individual files. Without this,
        # filesAnalyzed defaults to true and SPDX then *requires* a
        # packageVerificationCode + file list per package (SPDX 2.3 §7.9).
        "filesAnalyzed": False,
    })
    relationships.append({
        "spdxElementId": "SPDXRef-DOCUMENT",
        "relationshipType": "DESCRIBES",
        "relatedSpdxElement": root_id,
    })

    counter = 0

    def _add(name: str, version: str, purl: str,
             licenses: list[str] | None = None) -> None:
        nonlocal counter
        if not name:
            return
        counter += 1
        pid = f"SPDXRef-Package-{counter}"  # names aren't SPDXID-safe; use index
        entry: dict[str, Any] = {
            "SPDXID": pid,
            "name": name,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,  # see root package note
        }
        if version:
            entry["versionInfo"] = version
        if licenses:
            # FreeBSD codes aren't SPDX license ids, so we do NOT put them in
            # licenseDeclared (would be a malformed/guessed SPDX expression).
            # Record them faithfully as a free-text comment instead.
            entry["licenseComments"] = (
                "FreeBSD pkg license(s): " + ", ".join(licenses)
            )
        if purl:
            entry["externalRefs"] = [{
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": purl,
            }]
        packages.append(entry)
        relationships.append({
            "spdxElementId": root_id,
            "relationshipType": "CONTAINS",
            "relatedSpdxElement": pid,
        })

    for pkg in pkgs.get("freebsd", []):
        name, version = pkg.get("name", ""), pkg.get("version", "")
        _add(name, version, _freebsd_purl(name, version, arch),
             pkg.get("licenses"))
    for category, entries in pkgs.items():
        if category == "freebsd":
            continue
        for pkg in entries:
            _add(pkg.get("name", ""), pkg.get("version", ""), pkg.get("purl", ""))

    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{image}-{tag}",
        "documentNamespace": f"https://spdx.daemonless.io/{image}/{tag}/{generated}",
        "creationInfo": {
            "created": generated,
            "creators": [f"Tool: dbuild-{VERSION}"],
        },
        "comment": _FREEBSD_CVE_CAVEAT,
        "packages": packages,
        "relationships": relationships,
    }


# Implemented output formats. "all" expands to every entry here; validation
# and output order both follow this tuple.
# "daemonless" is the bespoke native schema (written as <stem>-sbom.json).
_KNOWN_FORMATS = ("daemonless", "cyclonedx", "spdx")
# What an empty/absent --format falls back to (mirrors the CLI arg default).
# Kept distinct from _KNOWN_FORMATS so adding a new format doesn't silently
# opt it into the default set.
_DEFAULT_FORMATS = ("daemonless", "cyclonedx")


# Where the SBOM is baked into the image (mirrors FreeBSD's own convention
# of shipping SBOMs under /usr/share/sbom/).
_EMBED_DIR = "/usr/share/sbom"


def _embed_sbom(build_ref: str, files: list[Path]) -> None:
    """Bake *files* into *build_ref* at ``/usr/share/sbom/`` via buildah.

    Commits back onto the same ``build-{tag}`` tag so a subsequent ``push``
    ships an image that carries its own SBOM. The buildah working container
    is always removed, even on error.
    """
    if not files:
        return
    log.info(f"Embedding SBOM into {build_ref} at {_EMBED_DIR}/")
    container_id = podman.bah_from(build_ref)
    try:
        for f in files:
            podman.bah_copy(container_id, str(f), f"{_EMBED_DIR}/{f.name}")
        podman.bah_commit(container_id, build_ref)
    finally:
        podman.bah_rm(container_id)


def _parse_formats(raw: str | None) -> list[str]:
    """Parse the ``--format`` value into an ordered, de-duplicated list.

    Accepts a comma-separated list of ``daemonless``, ``cyclonedx``, ``spdx``,
    or ``all``. Unknown formats raise ``ValueError``. Order follows
    ``_KNOWN_FORMATS``.
    """
    tokens = {t.strip() for t in (raw or "").split(",") if t.strip()}
    if not tokens:
        tokens = set(_DEFAULT_FORMATS)
    if "all" in tokens:
        tokens.discard("all")
        tokens.update(_KNOWN_FORMATS)
    unknown = tokens - set(_KNOWN_FORMATS)
    if unknown:
        raise ValueError(
            f"Unknown SBOM format(s): {', '.join(sorted(unknown))}. "
            f"Valid: {', '.join(_KNOWN_FORMATS)}, all"
        )
    return [f for f in _KNOWN_FORMATS if f in tokens]


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

    try:
        formats = _parse_formats(getattr(args, "format", None))
    except ValueError as exc:
        log.error(str(exc))
        return

    generated: list[str] = []

    for variant in cfg.variants:
        if variant_filter and variant.tag != variant_filter:
            continue

        sbom = _generate_sbom(cfg, variant, arch)
        # Include the arch suffix (matching the internal `tag`) so per-arch
        # builds -- which run in separate VM jobs/hosts -- don't collide on a
        # shared filename. amd64 is the default and stays bare for back-compat
        # (e.g. base-core-15.1-pkg-sbom.json); aarch64 -> ...-pkg-aarch64-...
        stem = f"{cfg.image}-{variant.tag}{arch_tag_suffix(arch)}"

        # The daemonless-native schema (Mode A) and standards formats
        # (Mode B) all derive from the same scan -- serialization is free.
        # The native format keeps its historical <stem>-sbom.json filename
        # (consumed by daemonless.io + CI), regardless of the token name.
        # The standards docs are delivered via the image (--embed) and the
        # registry attestation, NOT as sibling files, so sbom.json carries no
        # pointer to them (that would name a file not present in git).
        log.step("SBOM Complete")
        log.info(f"Summary: {json.dumps(sbom['summary'])}")
        embed_files: list[Path] = []
        for fmt in formats:
            if fmt == "daemonless":
                filename, doc = f"{stem}-sbom.json", sbom
            elif fmt == "cyclonedx":
                filename, doc = f"{stem}-cyclonedx.json", _generate_cyclonedx(sbom)
            else:  # spdx
                filename, doc = f"{stem}-spdx.json", _generate_spdx(sbom)
            path = output_dir / filename
            with open(path, "w") as fh:
                json.dump(doc, fh, indent=2)
                fh.write("\n")
            log.success(f"Output: {path}")
            generated.append(str(path))
            # Only the standards formats get baked into the image; the native
            # schema stays in git, not shipped inside the container.
            if fmt in ("cyclonedx", "spdx"):
                embed_files.append(path)

        if getattr(args, "embed", False):
            _embed_sbom(f"{cfg.full_image}:build-{variant.tag}", embed_files)

    if not generated:
        log.warn("No variants matched the filter")
        return

    log.step("SBOM generation summary")
    for path in generated:
        log.success(f"  {path}")
