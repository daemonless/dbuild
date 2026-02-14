"""Push orchestration for built container images.

For each variant (optionally filtered by ``--variant``):

1. Detects the CI environment and checks if this is a PR build (skips push).
2. Logs in to the primary registry.
3. Tags the ``build-{tag}`` image with the final tag and any aliases.
4. Pushes all tags.
5. Mirrors to a secondary Docker Hub registry when ``DOCKERHUB_USERNAME``
   is set in the environment.

This module does NOT build or test.  It uses :mod:`dbuild.registry` backends
and :mod:`dbuild.ci` for environment detection.
"""

from __future__ import annotations

import argparse
import os

from dbuild import ci as ci_mod
from dbuild import log, podman
from dbuild import registry as registry_mod
from dbuild.config import Config, Variant


def _arch_suffix(arch: str) -> str:
    """Return the tag suffix for *arch* (empty for amd64)."""
    return f"-{arch}" if arch != "amd64" else ""


def _version_tag(version: str, variant_tag: str) -> str:
    """Build a version tag like ``32.0.5`` or ``32.0.5-pkg``."""
    v = version.lstrip("v")
    if variant_tag == "latest":
        return v
    return f"{v}-{variant_tag}"


def _collect_tags(
    variant: Variant,
    arch: str,
    version: str | None = None,
) -> list[str]:
    """Return all tags that should be pushed for *variant* and *arch*.

    The primary tag is always first, followed by any aliases,
    then the versioned tag (e.g. ``32.0.5-pkg``).
    Non-amd64 architectures get an arch suffix (e.g. ``15-aarch64``).
    """
    suffix = _arch_suffix(arch)
    tags = [f"{variant.tag}{suffix}"]
    for alias in variant.aliases:
        suffixed = f"{alias}{suffix}"
        if suffixed not in tags:
            tags.append(suffixed)
    if version:
        vtag = f"{_version_tag(version, variant.tag)}{suffix}"
        if vtag not in tags:
            tags.append(vtag)
    return tags


def _push_variant(
    cfg: Config,
    variant: Variant,
    arch: str,
    *,
    reg: registry_mod.RegistryBase,
    mirror_reg: registry_mod.RegistryBase | None = None,
) -> None:
    """Tag and push a single variant to the primary (and optional mirror) registry."""
    build_ref = f"{cfg.full_image}:build-{variant.tag}"

    # Read version from OCI label applied during build.
    labels = podman.inspect_labels(build_ref)
    version = labels.get("org.opencontainers.image.version")

    tags = _collect_tags(variant, arch, version)

    log.step(f"Pushing :{variant.tag}")

    # Tag and push to primary registry.
    for tag in tags:
        final_ref = f"{cfg.full_image}:{tag}"
        log.info(f"Tagging {build_ref} -> {final_ref}")
        podman.tag(build_ref, final_ref)
        reg.push(cfg.full_image, tag)

    # Mirror to secondary registry (e.g. Docker Hub).
    if mirror_reg is not None:
        log.step(f"Mirroring :{variant.tag} to secondary registry")
        for tag in tags:
            src_ref = f"{cfg.full_image}:{tag}"
            # Derive the mirror image name by replacing the primary registry
            # prefix with the mirror registry URL.
            mirror_image = src_ref.replace(cfg.registry, mirror_reg.url, 1)
            mirror_reg.copy(src_ref, mirror_image)


def run(cfg: Config, args: argparse.Namespace) -> None:
    """Push all (or filtered) variants.

    Parameters
    ----------
    cfg:
        Parsed build configuration.
    args:
        CLI arguments.  Recognised attributes:

        * ``variant`` -- push only this tag (optional).
    """
    # ---- CI detection ----
    ci = ci_mod.detect()

    if ci.should_skip("push"):
        log.info("Skipping push ([skip push] in commit message)")
        return

    if ci.is_pr():
        log.warn("Pull-request build detected -- skipping push")
        return

    # ---- primary registry login ----
    token = ci.get_token()
    actor = ci.get_actor()
    primary_reg = registry_mod.for_url(cfg.registry, token)

    if token and actor:
        primary_reg.login(token, actor)
    else:
        log.warn(
            "No token/actor available -- assuming already logged in "
            "(set GITHUB_TOKEN / GITHUB_ACTOR for automatic login)"
        )

    # ---- optional Docker Hub mirror ----
    mirror_reg: registry_mod.RegistryBase | None = None
    dh_username = os.environ.get("DOCKERHUB_USERNAME")
    dh_token = os.environ.get("DOCKERHUB_TOKEN")
    if ci.should_skip("push:dockerhub"):
        log.info("Skipping Docker Hub mirror ([skip push:dockerhub] in commit message)")
    elif dh_username and dh_token:
        log.info("Docker Hub mirroring enabled")
        # Extract org from primary registry (e.g. ghcr.io/daemonless -> daemonless)
        parts = cfg.registry.split("/", 1)
        dh_org = dh_username if len(parts) < 2 else parts[1]
        mirror_reg = registry_mod.for_url(f"docker.io/{dh_org}", dh_token)
        mirror_reg.login(dh_token, dh_username)

    # ---- push each variant ----
    variant_filter: str | None = getattr(args, "variant", None)
    arch: str = getattr(args, "arch", None) or cfg.architectures[0]
    pushed: list[str] = []

    for variant in cfg.variants:
        if variant_filter and variant.tag != variant_filter:
            continue
        _push_variant(
            cfg,
            variant,
            arch,
            reg=primary_reg,
            mirror_reg=mirror_reg,
        )
        pushed.append(variant.tag)

    if not pushed:
        log.warn("No variants matched the filter")
        return

    log.step("Push summary")
    for tag in pushed:
        log.success(f"  :{tag}")
