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
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    *,
    prefix: str | None = None,
    no_cache: bool = False,
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
        prefix=prefix,
        no_cache=no_cache,
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

        * ``variant``  -- build only this tag (optional).
        * ``arch``     -- target architecture override (optional).
        * ``parallel`` -- max concurrent builds; None means sequential.
    """
    if cfg.type == "stack":
        log.info("type: stack — nothing to build")
        return

    variant_filter: str | None = getattr(args, "variant", None)
    arch: str = getattr(args, "arch", None) or cfg.architectures[0]
    parallel: int | None = getattr(args, "parallel", None)
    no_cache: bool = getattr(args, "no_cache", False)

    variants = [
        v for v in cfg.variants
        if not variant_filter or v.tag == variant_filter
    ]

    if not variants:
        log.warn("No variants matched the filter")
        return

    built: list[str] = []

    if parallel is not None and len(variants) > 1:
        workers = parallel if parallel > 0 else len(variants)
        # Pad tag labels to the same width for aligned output
        max_tag_len = max(len(v.tag) for v in variants)
        log.step(f"Building {len(variants)} variants in parallel (workers={workers})")

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for variant in variants:
                prefix = f"[{variant.tag:<{max_tag_len}}] "
                futures[executor.submit(_build_variant, cfg, variant, arch, prefix=prefix, no_cache=no_cache)] = variant.tag

            for future in as_completed(futures):
                tag = futures[future]
                try:
                    ref = future.result()
                    built.append(ref)
                except Exception as exc:
                    log.error(f":{tag} failed: {exc}")
                    raise
    else:
        for variant in variants:
            ref = _build_variant(cfg, variant, arch, no_cache=no_cache)
            built.append(ref)

    log.step("Build summary")
    for ref in built:
        log.success(f"  {ref}")
