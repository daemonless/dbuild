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
import json
import os
import subprocess

from dbuild import ci as ci_mod
from dbuild import log, podman
from dbuild import registry as registry_mod
from dbuild.config import Config, Variant, arch_tag_suffix, variant_filter_matches
from dbuild.push import _version_tag

# ── Architecture tag suffix convention ────────────────────────────────
# Uses the shared `config.arch_tag_suffix` so the tags created by `push`
# are exactly the ones looked up here (amd64 bare, others ``-<arch>``).

def _arch_tag(base_tag: str, arch: str, architectures: list[str] | None = None) -> str:
    """Return the architecture-specific tag for *base_tag*.

    Examples (single-arch image, or *architectures* not given):
        _arch_tag("latest", "amd64")    -> "latest"
        _arch_tag("latest", "aarch64")  -> "latest-aarch64"
        _arch_tag("pkg", "riscv64")     -> "pkg-riscv64"

    Multi-arch image (``len(architectures) > 1``): amd64 is suffixed too,
    e.g. ``_arch_tag("latest", "amd64", ["amd64", "aarch64"]) -> "latest-amd64"``.
    """
    return f"{base_tag}{arch_tag_suffix(arch, architectures)}"


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


def _manifest_annotate_index(manifest: str, annotations: dict[str, str]) -> None:
    """Set index-level annotations on a manifest list (best-effort).

    GHCR reads ``org.opencontainers.image.description`` for the per-tag
    package page from the index annotations of a manifest list.  Requires
    ``podman manifest annotate --index`` (podman >= 4.6); warns and
    continues on older versions.
    """
    for key, val in annotations.items():
        cmd = [
            *podman._priv_prefix(),
            "podman", "manifest", "annotate", "--index",
            "--annotation", f"{key}={val}", manifest,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            log.warn(
                f"manifest annotate failed for {key} "
                f"(podman too old for --index?): {result.stderr.strip()}"
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
    """Check whether *image_ref* exists in the remote registry.

    Remote-only, deliberately: a local-first check let a persistent
    host-mode runner's stale local images win over the real registry
    content, clobbering the amd64 slot of a manifest with a stale arm64
    image (2026-08-07 incident). Never consult the local podman store here.
    """
    cmd = [*podman._priv_prefix(), "skopeo", "inspect", f"docker://{image_ref}"]
    remote = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return remote.returncode == 0


def _remote_image_version(image_ref: str) -> str | None:
    """Read the ``org.opencontainers.image.version`` label from a remote image."""
    cmd = [*podman._priv_prefix(), "skopeo", "inspect", f"docker://{image_ref}"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return (data.get("Labels") or {}).get("org.opencontainers.image.version") or None


# ── Create manifest for one tag ──────────────────────────────────────

def _assemble_and_push(cfg: Config, image_prefix: str, tag: str, arch_refs: list[str]) -> bool:
    """Create, annotate, and push one manifest list from already-resolved *arch_refs*."""
    manifest_name = f"{image_prefix}:{tag}"
    _manifest_rm(manifest_name)
    _manifest_create(manifest_name)

    for ref in arch_refs:
        _manifest_add(manifest_name, ref)

    # Index annotations (GHCR per-tag description).
    if cfg.metadata.description:
        _manifest_annotate_index(manifest_name, {
            "org.opencontainers.image.description": cfg.metadata.description,
        })

    _manifest_push(manifest_name)
    return True


def _create_manifest_for_tag(
    cfg: Config,
    tag: str,
    mirror_url: str | None = None,
) -> bool:
    """Create and push a multi-arch manifest for a single tag.

    If *mirror_url* is set (a Docker Hub org URL, e.g. ``docker.io/daemonless``),
    also assembles and pushes the same manifest there, referencing the
    arch-specific images push.py already mirrored -- mirroring is skipped
    (not failed) if an arch's mirrored image isn't there yet.

    Returns True if the primary (GHCR) manifest was successfully created
    and pushed. The mirror manifest's success/failure doesn't affect this.
    """
    log.step(f"Manifest :{tag}")

    # Collect architecture-specific image references.
    arch_refs: list[str] = []
    mirror_refs: list[str] = []
    for arch in cfg.architectures:
        arch_specific_tag = _arch_tag(tag, arch, cfg.architectures)
        image_ref = f"{cfg.full_image}:{arch_specific_tag}"
        if _image_available(image_ref):
            log.info(f"Found: {image_ref}")
            arch_refs.append(image_ref)
        else:
            log.warn(f"Not found: {image_ref} (skipping)")

        if mirror_url:
            mirror_ref = f"{mirror_url}/{cfg.image}:{arch_specific_tag}"
            if _image_available(mirror_ref):
                mirror_refs.append(mirror_ref)

    if not arch_refs:
        log.error(f"No architecture-specific images found for :{tag}")
        return False

    ok = _assemble_and_push(cfg, cfg.full_image, tag, arch_refs)

    if mirror_url:
        if mirror_refs:
            log.step(f"Mirroring manifest :{tag} to {mirror_url}")
            _assemble_and_push(cfg, f"{mirror_url}/{cfg.image}", tag, mirror_refs)
        else:
            log.warn(f"No mirrored images found on {mirror_url} for :{tag} -- skipping mirror manifest")

    return ok


def _create_versioned_manifest(
    cfg: Config,
    variant: Variant,
    mirror_url: str | None = None,
) -> bool:
    """Create and push a manifest for *variant*'s versioned tag (e.g. ``2.9.0_2-pkg-latest``).

    push.py already writes this exact tag per-arch (e.g. ``2.9.0_2-pkg-latest-amd64``),
    but nothing previously stitched those into a combined manifest-list tag --
    version trackers reading the registry had nothing current to find, only
    stale bare-version tags left over from before an image went multi-arch
    (smokeping 2026-08-10). Version is read from an already-pushed arch
    image's OCI label, the same source push.py trusts. Skips silently (no
    versioned tag pushed) if the version can't be determined -- a missing
    tag is safer than a wrong one.
    """
    arch_refs: list[str] = []
    mirror_refs: list[str] = []
    version: str | None = None
    for arch in cfg.architectures:
        arch_specific_tag = _arch_tag(variant.tag, arch, cfg.architectures)
        image_ref = f"{cfg.full_image}:{arch_specific_tag}"
        if _image_available(image_ref):
            arch_refs.append(image_ref)
            if version is None:
                version = _remote_image_version(image_ref)
        if mirror_url:
            mirror_ref = f"{mirror_url}/{cfg.image}:{arch_specific_tag}"
            if _image_available(mirror_ref):
                mirror_refs.append(mirror_ref)

    if not arch_refs:
        return False
    if not version:
        log.warn(f"Could not determine version for :{variant.tag} -- skipping versioned manifest")
        return False

    vtag = _version_tag(version, variant.tag)
    log.step(f"Manifest :{vtag}")
    ok = _assemble_and_push(cfg, cfg.full_image, vtag, arch_refs)

    if mirror_url and mirror_refs:
        _assemble_and_push(cfg, f"{mirror_url}/{cfg.image}", vtag, mirror_refs)

    return ok


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

    # ---- optional Docker Hub mirror ----
    # Same env vars and org-derivation as push.py, so a repo that's
    # mirroring pushes there also gets a mirrored manifest for free.
    mirror_url: str | None = None
    dh_username = os.environ.get("DOCKERHUB_USERNAME")
    dh_token = os.environ.get("DOCKERHUB_TOKEN")
    if dh_username and dh_token:
        parts = cfg.registry.split("/", 1)
        dh_org = dh_username if len(parts) < 2 else parts[1]
        mirror_reg = registry_mod.for_url(f"docker.io/{dh_org}", dh_token)
        mirror_reg.login(dh_token, dh_username)
        mirror_url = mirror_reg.url

    # ---- collect all tags that need manifests ----
    all_tags: list[str] = []
    variant_filter: str | None = getattr(args, "variant", None)

    for variant in cfg.variants:
        if not variant_filter_matches(variant.tag, variant_filter):
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
        ok = _create_manifest_for_tag(cfg, tag, mirror_url)
        if ok:
            created.append(tag)
        else:
            failed.append(tag)

    # ---- versioned manifests (e.g. 2.9.0_2-pkg-latest), one per variant ----
    for variant in cfg.variants:
        if not variant_filter_matches(variant.tag, variant_filter):
            continue
        vtag_ok = _create_versioned_manifest(cfg, variant, mirror_url)
        if vtag_ok:
            created.append(f"{variant.tag} (versioned)")

    # ---- summary ----
    log.step("Manifest summary")
    for tag in created:
        log.success(f"  :{tag}")
    for tag in failed:
        log.error(f"  :{tag} (failed)")
