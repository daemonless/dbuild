"""CI pipeline orchestration.

Replaces ``dbuild-ci.sh`` with a pure-Python implementation that calls
the build, test, push, and sbom modules directly.  Runs the full
pipeline: build -> test -> push -> sbom.

On PR builds, push and sbom are skipped.  Commit-message directives
like ``[skip test]`` and ``[skip push]`` are honoured by the
individual modules.
"""

from __future__ import annotations

import argparse

from dbuild import build, log, prepare, push, sbom, test
from dbuild import ci as ci_mod
from dbuild.config import Config


def run(cfg: Config, args: argparse.Namespace) -> int:
    """Run the full CI pipeline.

    Parameters
    ----------
    cfg:
        Parsed build configuration.
    args:
        CLI arguments.  Recognised attributes:

        * ``variant``  -- build only this tag (optional).
        * ``arch``     -- target architecture override (optional).
        * ``prepare``  -- run ci-prepare first (optional).

    Returns ``0`` on success, non-zero on failure.
    """
    # Optional: run ci-prepare first
    if getattr(args, "prepare", False):
        rc = prepare.run(args)
        if rc != 0:
            return rc

    # ── Build ────────────────────────────────────────────────────────
    log.step("CI: Build")
    rc = build.run(cfg, args)
    if rc and rc != 0:
        log.error("Build failed")
        return rc

    # ── Test ─────────────────────────────────────────────────────────
    log.step("CI: Test")

    # Ensure json_output is set for CI artifact preservation
    if not hasattr(args, "json_output"):
        args.json_output = "cit-result.json"

    rc = test.run(cfg, args)
    if rc and rc != 0:
        log.error("Tests failed -- skipping push and sbom")
        return rc

    # ── PR detection: skip push + sbom on pull requests ──────────────
    backend = ci_mod.detect()

    if backend.is_pr():
        log.info("Pull-request build -- skipping push and sbom")
        log.success("CI pipeline complete (PR)")
        return 0

    # ── Push ─────────────────────────────────────────────────────────
    log.step("CI: Push")
    rc = push.run(cfg, args)
    if rc and rc != 0:
        log.error("Push failed")
        return rc

    # ── SBOM ─────────────────────────────────────────────────────────
    log.step("CI: SBOM")
    rc = sbom.run(cfg, args)
    if rc and rc != 0:
        log.error("SBOM generation failed")
        return rc

    log.success("CI pipeline complete")
    return 0
