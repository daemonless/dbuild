"""Multi-arch manifest creation and push.

For each variant tag (plus aliases), this module:

1. Builds a list of architecture-specific image references.
2. Creates a podman manifest via ``podman manifest create``.
3. Adds each architecture's image via ``podman manifest add``.
4. Pushes the manifest via ``podman manifest push``.

Uses registry backends from :mod:`dbuild.registry` for login.
"""

from __future__ import annotations

import argparse
import subprocess

from dbuild import ci as ci_mod
from dbuild import log, podman
from dbuild import registry as registry_mod
from dbuild.config import Config

# ── Architecture tag suffix convention ────────────────────────────────
# amd64 images have no suffix (bare tag); non-amd64 images are suffixed.

_ARCH_TAG_SUFFIX: dict[str, str] = {
    "amd64": "",
    "aarch64": "-arm64",
    "arm64": "-arm64",
    "riscv64": "-riscv64",
}


def _arch_tag(base_tag: str, arch: str) -> str:
    """Return the architecture-specific tag for *base_tag*.

    Examples:
        _arch_tag("latest", "amd64")    -> "latest"
        _arch_tag("latest", "aarch64")  -> "latest-arm64"
        _arch_tag("pkg", "riscv64")     -> "pkg-riscv64"
    """
    suffix = _ARCH_TAG_SUFFIX.get(arch)
    if suffix is None:
        log.warn(f"Unknown architecture {arch} for tag suffix, using -{arch}")
        suffix = f"-{arch}"
    return f"{base_tag}{suffix}"


# ── Podman manifest helpers ──────────────────────────────────────────
# These are thin wrappers that log and raise on failure.  They stay here
# (rather than in podman.py) because podman.py has zero business logic
# and manifest operations are orchestration-level concerns.

def _manifest_rm(name: str) -> None:
    """Remove a manifest list if it exists (best-effort)."""
    cmd = [*podman._priv_prefix(), "podman", "manifest", "rm", name]
    subprocess.run(cmd, capture_output=True, text=True, check=False)


def _manifest_create(name: str) -> None:
    """Create a new manifest list."""
    log.info(f"Creating manifest: {name}")
    cmd = [*podman._priv_prefix(), "podman", "manifest", "create", name]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"podman manifest create failed: {result.stderr.strip()}"
        )


def _manifest_add(manifest: str, image_ref: str) -> None:
    """Add an image to a manifest list."""
    log.info(f"Adding to manifest: {image_ref}")
    cmd = [
        *podman._priv_prefix(),
        "podman", "manifest", "add", manifest, f"docker://{image_ref}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"podman manifest add failed for {image_ref}: {result.stderr.strip()}"
        )


def _manifest_push(manifest: str) -> None:
    """Push a manifest list to the registry."""
    log.info(f"Pushing manifest: {manifest}")
    cmd = [
        *podman._priv_prefix(),
        "podman", "manifest", "push", "--all", manifest, f"docker://{manifest}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"podman manifest push failed: {result.stderr.strip()}"
        )
    log.success(f"Pushed manifest: {manifest}")


def _image_available(image_ref: str) -> bool:
    """Check whether *image_ref* is available locally or in a remote registry."""
    # Try local first.
    if podman.image_exists(image_ref):
        return True

    # Try remote via skopeo.
    cmd = [*podman._priv_prefix(), "skopeo", "inspect", f"docker://{image_ref}"]
    remote = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return remote.returncode == 0


# ── Create manifest for one tag ──────────────────────────────────────

def _create_manifest_for_tag(
    cfg: Config,
    tag: str,
) -> bool:
    """Create and push a multi-arch manifest for a single tag.

    Returns True if the manifest was successfully created and pushed.
    """
    manifest_name = f"{cfg.full_image}:{tag}"
    log.step(f"Manifest :{tag}")

    # Collect architecture-specific image references.
    arch_refs: list[str] = []
    for arch in cfg.architectures:
        arch_specific_tag = _arch_tag(tag, arch)
        image_ref = f"{cfg.full_image}:{arch_specific_tag}"
        if _image_available(image_ref):
            log.info(f"Found: {image_ref}")
            arch_refs.append(image_ref)
        else:
            log.warn(f"Not found: {image_ref} (skipping)")

    if not arch_refs:
        log.error(f"No architecture-specific images found for :{tag}")
        return False

    # Remove any stale manifest, create a fresh one, add images.
    _manifest_rm(manifest_name)
    _manifest_create(manifest_name)

    for ref in arch_refs:
        _manifest_add(manifest_name, ref)

    # Push.
    _manifest_push(manifest_name)
    return True


# ── Public API ────────────────────────────────────────────────────────

def run(cfg: Config, args: argparse.Namespace) -> None:
    """Create and push multi-arch manifests for all variants.

    For each variant, a manifest is created for the primary tag and for
    every alias.  The CI backend is used for registry login.

    Parameters
    ----------
    cfg:
        Parsed build configuration.
    args:
        CLI arguments (currently unused but accepted for interface
        consistency with other command modules).
    """
    if len(cfg.architectures) < 2:
        log.warn(
            f"Only one architecture configured ({cfg.architectures}) -- "
            "manifest creation is only useful for multi-arch images"
        )

    # ---- registry login ----
    ci = ci_mod.detect()
    token = ci.get_token()
    actor = ci.get_actor()
    primary_reg = registry_mod.for_url(cfg.registry, token)

    if token and actor:
        primary_reg.login(token, actor)
    else:
        log.warn(
            "No token/actor available -- assuming already logged in"
        )

    # ---- collect all tags that need manifests ----
    all_tags: list[str] = []
    variant_filter: str | None = getattr(args, "variant", None)

    for variant in cfg.variants:
        if variant_filter and variant.tag != variant_filter:
            continue
        if variant.tag not in all_tags:
            all_tags.append(variant.tag)
        for alias in variant.aliases:
            if alias not in all_tags:
                all_tags.append(alias)

    if not all_tags:
        log.warn("No tags to create manifests for")
        return

    # ---- create and push manifests ----
    created: list[str] = []
    failed: list[str] = []

    for tag in all_tags:
        ok = _create_manifest_for_tag(cfg, tag)
        if ok:
            created.append(tag)
        else:
            failed.append(tag)

    # ---- summary ----
    log.step("Manifest summary")
    for tag in created:
        log.success(f"  :{tag}")
    for tag in failed:
        log.error(f"  :{tag} (failed)")
