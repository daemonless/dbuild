"""Build orchestration for container images.

For each variant (optionally filtered by ``--variant`` / ``--arch``),
this module:

1. Maps the target architecture to the FreeBSD convention.
2. Assembles build arguments (including ``FREEBSD_ARCH`` and ``BASE_VERSION``).
3. Calls :func:`podman.build` with secrets for ``GITHUB_TOKEN``.
4. Tags the result as ``{full_image}:build-{tag}``.
5. Extracts the application/base version via :mod:`version`.
6. Applies OCI labels via :mod:`labels`.

This module does NOT push, test, or know about CI systems.
"""

from __future__ import annotations

import argparse
import os

from dbuild import labels, log, podman, version
from dbuild.config import Config, Variant

# ── Architecture mapping ─────────────────────────────────────────────

_ARCH_MAP: dict[str, str] = {
    "amd64": "amd64",
    "x86_64": "amd64",
    "x64": "amd64",
    "arm64": "aarch64",
    "aarch64": "aarch64",
    "riscv64": "riscv64",
    "riscv": "riscv64",
}


def _map_arch(arch: str) -> str:
    """Map a user-supplied architecture name to the FreeBSD convention.

    Raises ``ValueError`` for unrecognised values.
    """
    mapped = _ARCH_MAP.get(arch)
    if mapped is None:
        supported = ", ".join(sorted(set(_ARCH_MAP.values())))
        raise ValueError(
            f"Unknown architecture: {arch}  (supported: {supported})"
        )
    return mapped


# ── Build a single variant ───────────────────────────────────────────

def _build_variant(
    cfg: Config,
    variant: Variant,
    arch: str,
) -> str:
    """Build one variant for one architecture.  Returns the build tag."""
    freebsd_arch = _map_arch(arch)
    build_tag = f"build-{variant.tag}"
    full_build_ref = f"{cfg.full_image}:{build_tag}"

    log.step(f"Building :{variant.tag}  (arch={freebsd_arch})")
    log.info(f"Containerfile: {variant.containerfile}")
    log.info(f"Image: {full_build_ref}")

    # ---- assemble build args ----
    build_args: dict[str, str] = {
        "FREEBSD_ARCH": freebsd_arch,
    }
    # Inject BASE_VERSION from variant args (if present).
    if "BASE_VERSION" in variant.args:
        build_args["BASE_VERSION"] = variant.args["BASE_VERSION"]
    # Merge any additional variant-specific build args.
    for key, val in variant.args.items():
        build_args.setdefault(key, val)

    # ---- secrets ----
    secrets: dict[str, str] = {}
    if os.environ.get("GITHUB_TOKEN"):
        secrets["github_token"] = "GITHUB_TOKEN"

    # ---- run the build ----
    log.timer_start(f"build:{variant.tag}")
    podman.build(
        containerfile=variant.containerfile,
        tag=full_build_ref,
        build_args=build_args,
        secrets=secrets,
    )
    log.timer_stop(f"build:{variant.tag}")

    # ---- extract version ----
    log.step(f"Extracting version for :{variant.tag}")
    app_version = version.extract_version(full_build_ref, cfg.type)
    if app_version:
        log.success(f"Version: {app_version}")
    else:
        log.warn("No version detected")

    # ---- apply OCI labels ----
    log.step(f"Applying labels for :{variant.tag}")
    oci_labels = labels.build_labels(
        version=app_version,
        variant_tag=variant.tag,
    )
    labels.apply(full_build_ref, oci_labels)

    return full_build_ref


# ── Public API ────────────────────────────────────────────────────────

def run(cfg: Config, args: argparse.Namespace) -> None:
    """Build all (or filtered) variants.

    Parameters
    ----------
    cfg:
        Parsed build configuration.
    args:
        CLI arguments.  Recognised attributes:

        * ``variant`` -- build only this tag (optional).
        * ``arch``    -- target architecture override (optional).
    """
    variant_filter: str | None = getattr(args, "variant", None)
    arch: str = getattr(args, "arch", None) or cfg.architectures[0]

    built: list[str] = []
    for variant in cfg.variants:
        if variant_filter and variant.tag != variant_filter:
            continue
        ref = _build_variant(cfg, variant, arch)
        built.append(ref)

    if not built:
        log.warn("No variants matched the filter")
        return

    log.step("Build summary")
    for ref in built:
        log.success(f"  {ref}")
