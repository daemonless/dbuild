"""Variant auto-detection and CI matrix output.

Reads config, generates the build matrix, and outputs it in a format
suitable for the detected CI system (GitHub Actions, Woodpecker, etc.)
or as plain JSON for local use.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from typing import Any

from dbuild import ci as ci_mod
from dbuild import log
from dbuild.config import Config

# Map architecture → vmactions settings for GitHub Actions FreeBSD VM
_VM_ARCH_MAP: dict[str, dict[str, str]] = {
    "amd64":   {"arch_suffix": "",          "vm_arch": "",        "vm_sync": "rsync"},
    "aarch64": {"arch_suffix": "-aarch64",  "vm_arch": "aarch64", "vm_sync": "rsync"},
    "riscv64": {"arch_suffix": "-riscv64",  "vm_arch": "riscv64", "vm_sync": "scp"},
}


def _build_matrix(cfg: Config, args: argparse.Namespace) -> list[dict[str, Any]]:
    """Build a matrix of (variant, arch) combinations."""
    matrix: list[dict[str, Any]] = []

    variant_filter: str | None = getattr(args, "variant", None)
    arch_filter: str | None = getattr(args, "arch", None)

    for variant in cfg.variants:
        if variant_filter and variant.tag != variant_filter:
            continue
        for arch in cfg.architectures:
            if arch_filter and arch != arch_filter:
                continue
            matrix.append({
                "tag": variant.tag,
                "containerfile": variant.containerfile,
                "arch": arch,
                "args": variant.args,
                "aliases": variant.aliases,
                "auto_version": variant.auto_version,
            })

    return matrix


def _github_extras(
    matrix: list[dict[str, Any]], cfg: Config
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Add VM-specific fields to matrix entries and compute extra outputs.

    Returns the enriched matrix and a dict of extra outputs to set
    (compose_only, architectures, manifest_tags).
    """
    enriched: list[dict[str, Any]] = []
    for entry in matrix:
        vm = _VM_ARCH_MAP.get(entry["arch"], _VM_ARCH_MAP["amd64"])
        enriched.append({
            **entry,
            "type": cfg.type,
            "arch_suffix": vm["arch_suffix"],
            "vm_arch": vm["vm_arch"],
            "vm_sync": vm["vm_sync"],
        })

    # compose_only: true when no variants but compose file exists
    compose_only = "false"
    if not matrix and cfg.test and cfg.test.compose:
        compose_only = "true"

    # architectures as JSON array
    arch_json = json.dumps(cfg.architectures, separators=(",", ":"))

    # manifest_tags: unique tag + aliases for multi-arch manifests
    manifest_tags: list[str] = []
    seen: set[str] = set()
    for v in cfg.variants:
        for t in [v.tag, *v.aliases]:
            if t not in seen:
                manifest_tags.append(t)
                seen.add(t)

    extras = {
        "compose_only": compose_only,
        "architectures": arch_json,
        "manifest_tags": " ".join(manifest_tags),
    }
    return enriched, extras


def _format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    if size_bytes >= 1_000_000_000:
        return f"{size_bytes / 1_000_000_000:.1f} GB"
    if size_bytes >= 1_000_000:
        return f"{size_bytes / 1_000_000:.0f} MB"
    return f"{size_bytes / 1_000:.0f} KB"


def _format_age(created: int) -> str:
    """Format a unix timestamp as relative age."""
    delta = int(time.time()) - created
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def _list_local_images(cfg: Config) -> tuple[list[dict], list[dict]]:
    """Query podman for local images and split into pushed vs build cache.

    Uses the config's variant list to determine which tags are build
    intermediates (``build-{tag}``) vs final pushed tags.

    Returns (pushed, build_cache) tuple.
    """
    from dbuild import podman

    try:
        cmd = [*podman._priv_prefix(), "podman", "images", "--format", "json", cfg.full_image]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0 or not result.stdout.strip():
            return [], []
        imgs = json.loads(result.stdout)
    except Exception:
        return [], []

    # Build the set of known build-cache tags from config
    build_tags = {f"build-{v.tag}" for v in cfg.variants}

    pushed = []
    cache = []
    for img in imgs:
        names = img.get("Names") or img.get("History") or []
        tag = "none"
        for name in names:
            if ":" in name:
                tag = name.rsplit(":", 1)[1]
                break
        labels = img.get("Labels") or {}
        version = labels.get("org.opencontainers.image.version", "")
        entry = {
            "tag": tag,
            "version": version,
            "size": _format_size(img.get("Size", 0)),
            "age": _format_age(img.get("Created", 0)),
        }
        if tag in build_tags:
            cache.append(entry)
        else:
            pushed.append(entry)

    return pushed, cache


def run(cfg: Config, args: argparse.Namespace) -> None:
    """Output detected build matrix.

    When ``--format`` is ``github`` or ``woodpecker``, output is written
    via the CI backend.  Otherwise, plain JSON is printed to stdout.
    """
    matrix = _build_matrix(cfg, args)
    fmt = getattr(args, "format", None)

    if fmt == "human" or getattr(args, "human", False):
        # Human-readable output (used by `dbuild info`)
        if not matrix:
            log.warn("No variants detected")
            return
        log.step(f"Image: {cfg.full_image}")
        log.info(f"Type: {cfg.type}")
        log.info(f"Architectures: {', '.join(cfg.architectures)}")
        log.info(f"Variants: {len(cfg.variants)}")
        for entry in matrix:
            log.info(f"  {entry['tag']} ({entry['arch']}) -> {entry['containerfile']}")
            if entry["args"]:
                for k, v in entry["args"].items():
                    log.info(f"    {k}={v}")
            if entry["aliases"]:
                log.info(f"    aliases: {', '.join(entry['aliases'])}")
        if cfg.test:
            log.info(f"Test: mode={cfg.test.mode} port={cfg.test.port}")

        # Show local images
        pushed, cache = _list_local_images(cfg)
        if pushed or cache:
            all_imgs = pushed + cache
            tag_w = max(len(i["tag"]) for i in all_imgs)
            size_w = max(len(i["size"]) for i in all_imgs)
            age_w = max(len(i["age"]) for i in all_imgs)
            if pushed:
                log.info("Local images:")
                for img in pushed:
                    ver = f"  {img['version']}" if img["version"] else ""
                    log.info(
                        f"  :{img['tag']:<{tag_w}}  {img['size']:>{size_w}}"
                        f"  {img['age']:>{age_w}}{ver}"
                    )
            if cache:
                log.info("Build cache:")
                for img in cache:
                    ver = f"  {img['version']}" if img["version"] else ""
                    log.info(
                        f"  :{img['tag']:<{tag_w}}  {img['size']:>{size_w}}"
                        f"  {img['age']:>{age_w}}{ver}"
                    )
        else:
            log.info("Local images: none")
        return

    if fmt == "github":
        backend = ci_mod.detect()
        enriched, extras = _github_extras(matrix, cfg)
        backend.output_matrix(enriched)
        for key, value in extras.items():
            backend.set_output(key, value)
        return

    if fmt in ("woodpecker", "gitlab"):
        backend = ci_mod.detect()
        backend.output_matrix(matrix)
        return

    # Default: plain JSON to stdout
    if not matrix:
        log.warn("No variants detected")
        return
    json.dump({"include": matrix}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    sys.stdout.flush()
