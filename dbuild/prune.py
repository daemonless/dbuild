"""Clean up orphaned CIT containers and build images for the current project."""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
from typing import TYPE_CHECKING

from dbuild import appjail, log, podman

if TYPE_CHECKING:
    from dbuild.config import Config


# ── Collectors ────────────────────────────────────────────────────────

def _collect_cit_containers(image_name: str) -> list[str]:
    """Find leftover CIT Podman containers for this project."""
    suffix = f"-{image_name}"
    try:
        return [
            c for c in podman.list_containers()
            if c.startswith("cit-") and c.endswith(suffix)
        ]
    except Exception:
        return []


def _collect_cit_jails(image_name: str) -> list[str]:
    """Find leftover CIT AppJail jails for this project."""
    suffix = f"-{image_name}"
    try:
        return [
            j for j in appjail.list_jails()
            if j.startswith("cit-") and j.endswith(suffix)
        ]
    except Exception:
        return []


def _collect_build_images(full_image: str, variant: str | None) -> list[str]:
    """Find build-tagged images for this project.

    Returns a list of fully-qualified image references like
    ``ghcr.io/daemonless/radarr:build-latest``.
    """
    tag_pat = f"build-{variant}" if variant else "build-*"
    try:
        imgs = podman.images(f"reference={full_image}:{tag_pat}")
    except Exception:
        return []

    refs: list[str] = []
    for img in imgs:
        for name in (img.get("Names") or []):
            # Guard: must belong to this project and be a build- tag
            repo, _, tag = name.rpartition(":")
            if repo == full_image and tag.startswith("build-"):
                refs.append(name)
                break
    return refs


def _collect_tmp_files() -> list[str]:
    """Find leftover dbuild CIT temp files in /tmp."""
    return glob.glob("/tmp/dbuild-cit-*")


# ── Main entry point ─────────────────────────────────────────────────

def run(cfg: Config, args: argparse.Namespace) -> int:
    """Find and remove leftover CIT containers, jails, and build images."""
    image_name = cfg.image
    full_image = cfg.full_image
    variant: str | None = getattr(args, "variant", None)
    dry_run: bool = getattr(args, "dry_run", False)
    force: bool = getattr(args, "force", False)

    if not image_name:
        log.error("Could not determine project name")
        return 1

    containers = _collect_cit_containers(image_name)
    jails = _collect_cit_jails(image_name)
    images = _collect_build_images(full_image, variant)
    tmp_files = _collect_tmp_files()

    # Exclude build images that correspond to current config variants
    current_tags = {f"{full_image}:build-{v.tag}" for v in cfg.variants}
    if not variant:
        images = [ref for ref in images if ref not in current_tags]

    if not any([containers, jails, images, tmp_files]):
        log.info(f"Nothing to prune for '{image_name}'")
        return 0

    log.info(f"Resources to prune for '{image_name}':")
    for c in containers:
        log.info(f"  container  {c}")
    for j in jails:
        log.info(f"  jail       {j}")
    for ref in images:
        log.info(f"  image      {ref}")
    if tmp_files:
        log.info(f"  tmp        {len(tmp_files)} file(s) matching /tmp/dbuild-cit-*")

    if dry_run:
        log.info("Dry run — nothing removed.")
        return 0

    if not force:
        print()
        try:
            ans = input("Remove the above? [y/N] ").strip().lower()
        except EOFError:
            ans = "n"
        if ans != "y":
            log.info("Aborted.")
            return 0

    for c in containers:
        log.info(f"  stop/rm container: {c}")
        podman.stop(c)
        podman.rm(c)

    for j in jails:
        log.info(f"  stop/destroy jail: {j}")
        appjail.jail_stop(j)
        appjail.jail_destroy(j)

    for ref in images:
        log.info(f"  rmi: {ref}")
        podman.rmi(ref)

    for f in tmp_files:
        try:
            os.remove(f)
        except OSError:
            subprocess.call(
                [*podman._priv_prefix(), "rm", "-f", f],
                stderr=subprocess.DEVNULL,
            )

    log.success(f"Pruned resources for '{image_name}'")
    return 0
