"""Local-only promotion of built images.

For each variant (optionally filtered by ``--variant`` / ``--arch``), this
tags an already-built ``{full_image}:build-{tag}`` image with its final tag,
any aliases, and its version tag -- exactly what :mod:`dbuild.push` does,
minus the registry push.

This module does NOT touch the registry and needs no auth. Run ``dbuild
build`` (and optionally ``dbuild test``) first.
"""

from __future__ import annotations

import argparse

from dbuild import log
from dbuild.config import Config, default_arch, variant_filter_matches
from dbuild.push import _local_tag_variant


def run(cfg: Config, args: argparse.Namespace) -> int:
    """Promote all (or filtered) variants' build-{tag} images to their final tag.

    Parameters
    ----------
    cfg:
        Parsed build configuration.
    args:
        CLI arguments.  Recognised attributes:

        * ``variant`` -- promote only this tag (optional).
        * ``arch``    -- target architecture override (optional).
    """
    variant_filter: str | None = getattr(args, "variant", None)
    arch: str = getattr(args, "arch", None) or default_arch(cfg.architectures)

    variants = [
        v for v in cfg.variants
        if variant_filter_matches(v.tag, variant_filter)
    ]

    if not variants:
        log.warn("No variants matched the filter")
        return 0

    promoted: list[str] = []
    rc = 0
    for variant in variants:
        log.step(f"Promoting :{variant.tag}")
        try:
            tags = _local_tag_variant(cfg, variant, arch)
            promoted.extend(f"{variant.tag} -> :{tag}" for tag in tags)
        except RuntimeError as exc:
            log.error(str(exc))
            rc = 1

    log.step("Promote summary")
    for line in promoted:
        log.success(f"  {line}")

    return rc
