"""Command-line interface for dbuild.

This is the user-facing entry point.  It parses arguments, loads
configuration, and dispatches to the appropriate subcommand module.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import dbuild
from dbuild import log
from dbuild.config import Config
from dbuild.config import load as load_config

# ── Helpers ───────────────────────────────────────────────────────────

def _make_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands."""

    parser = argparse.ArgumentParser(
        prog="dbuild",
        description="FreeBSD OCI container image build tool",
        epilog="Run 'dbuild <command> --help' for subcommand-specific options.",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"dbuild {dbuild.VERSION}",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="enable debug logging",
    )
    parser.add_argument(
        "--variant",
        metavar="TAG",
        default=None,
        help="filter to a single variant by tag (e.g. latest, pkg)",
    )
    parser.add_argument(
        "--arch",
        metavar="ARCH",
        default=None,
        help="override target architecture (e.g. amd64, aarch64)",
    )
    parser.add_argument(
        "--registry",
        metavar="URL",
        default=None,
        help="override the container registry (e.g. ghcr.io/myorg)",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        default=False,
        help="push images after building (shorthand for build + push)",
    )

    sub = parser.add_subparsers(dest="command", title="commands")

    # Shared options that can appear after the subcommand too.
    # Use SUPPRESS so subcommand defaults don't overwrite global values.
    variant_kw = dict(metavar="TAG", default=argparse.SUPPRESS,
                      help="filter to a single variant by tag (e.g. latest, pkg)")
    arch_kw = dict(metavar="ARCH", default=argparse.SUPPRESS,
                   help="override target architecture (e.g. amd64, aarch64)")

    # -- build --
    build_parser = sub.add_parser(
        "build",
        help="build container image(s) for all (or selected) variants",
        description="Build container images from Containerfiles.",
    )
    build_parser.add_argument("--variant", **variant_kw)
    build_parser.add_argument("--arch", **arch_kw)

    # -- test --
    test_parser = sub.add_parser(
        "test",
        help="run CIT tests against built image(s)",
        description="Run container integration tests against built images.",
    )
    test_parser.add_argument("--variant", **variant_kw)
    test_parser.add_argument(
        "--json",
        metavar="FILE",
        default=argparse.SUPPRESS,
        dest="json_output",
        help="write test result JSON to FILE",
    )

    # -- push --
    push_parser = sub.add_parser(
        "push",
        help="push built image(s) to the registry",
        description="Tag and push built images to the configured registry.",
    )
    push_parser.add_argument("--variant", **variant_kw)
    push_parser.add_argument("--arch", **arch_kw)

    # -- sbom --
    sbom_parser = sub.add_parser(
        "sbom",
        help="generate SBOM for built image(s)",
        description="Generate a CycloneDX SBOM via trivy and pkg query.",
    )
    sbom_parser.add_argument("--variant", **variant_kw)
    sbom_parser.add_argument("--arch", **arch_kw)

    # -- manifest --
    sub.add_parser(
        "manifest",
        help="create and push multi-arch manifest lists",
        description="Create and push multi-architecture manifest lists.",
    )

    # -- detect --
    detect_parser = sub.add_parser(
        "detect",
        help="output build matrix as JSON (for CI integration)",
        description="Auto-detect variants and output the build matrix.",
    )
    detect_parser.add_argument(
        "--format", "-f",
        choices=["json", "github", "woodpecker", "gitlab"],
        default="json",
        dest="format",
        help="output format (default: json)",
    )

    # -- info --
    sub.add_parser(
        "info",
        help="show what would be built (human-readable overview)",
        description="Display detected configuration in a human-readable format.",
    )

    # -- init --
    init_parser = sub.add_parser(
        "init",
        help="scaffold a new dbuild project",
        description="Generate starter files for a new dbuild project.",
    )
    init_parser.add_argument(
        "--github",
        action="store_true",
        default=False,
        help="generate GitHub Actions workflow (.github/workflows/build.yaml)",
    )
    init_parser.add_argument(
        "--woodpecker",
        action="store_true",
        default=False,
        help="generate Woodpecker CI pipeline (.woodpecker.yaml)",
    )

    # -- ci-prepare --
    ci_prepare_parser = sub.add_parser(
        "ci-prepare",
        help="prepare FreeBSD CI environment (install packages, ocijail, networking)",
        description="Set up a FreeBSD CI runner with all build dependencies.",
    )
    ci_prepare_parser.add_argument("--arch", **arch_kw)
    ci_prepare_parser.add_argument(
        "--compose",
        action="store_true",
        default=False,
        help="also install podman-compose",
    )

    # -- ci-test-env --
    sub.add_parser(
        "ci-test-env",
        help="preflight check: verify build tools, networking, and runtime",
        description="Run read-only checks to verify the CI environment is ready.",
    )

    # -- ci-run --
    ci_run_parser = sub.add_parser(
        "ci-run",
        help="run full CI pipeline (build -> test -> push -> sbom)",
        description="Run the complete CI/CD pipeline for all (or selected) variants.",
    )
    ci_run_parser.add_argument("--variant", **variant_kw)
    ci_run_parser.add_argument("--arch", **arch_kw)
    ci_run_parser.add_argument(
        "--prepare",
        action="store_true",
        default=False,
        help="run ci-prepare before the pipeline",
    )

    return parser


def _apply_overrides(cfg: Config, args: argparse.Namespace) -> Config:
    """Apply CLI overrides (--registry, --arch) to the loaded config."""
    if args.registry is not None:
        cfg.registry = args.registry
    if args.arch is not None:
        cfg.architectures = [args.arch]
    return cfg



def _dispatch_build(cfg: Config, args: argparse.Namespace) -> int:
    """Run the build subcommand, optionally followed by push."""
    try:
        from dbuild import build
    except ImportError:
        log.error("build module is not yet implemented")
        return 1

    rc = build.run(cfg, args)
    if rc and rc != 0:
        return rc

    if args.push:
        return _dispatch_push(cfg, args)

    return 0


def _dispatch_test(cfg: Config, args: argparse.Namespace) -> int:
    """Run the test subcommand."""
    try:
        from dbuild import test
    except ImportError:
        log.error("test module is not yet implemented")
        return 1
    rc = test.run(cfg, args)
    return rc if rc else 0


def _dispatch_push(cfg: Config, args: argparse.Namespace) -> int:
    """Run the push subcommand."""
    try:
        from dbuild import push
    except ImportError:
        log.error("push module is not yet implemented")
        return 1
    rc = push.run(cfg, args)
    return rc if rc else 0


def _dispatch_sbom(cfg: Config, args: argparse.Namespace) -> int:
    """Run the sbom subcommand."""
    try:
        from dbuild import sbom
    except ImportError:
        log.error("sbom module is not yet implemented")
        return 1
    rc = sbom.run(cfg, args)
    return rc if rc else 0


def _dispatch_manifest(cfg: Config, args: argparse.Namespace) -> int:
    """Run the manifest subcommand."""
    try:
        from dbuild import manifest
    except ImportError:
        log.error("manifest module is not yet implemented")
        return 1
    rc = manifest.run(cfg, args)
    return rc if rc else 0


def _dispatch_detect(cfg: Config, args: argparse.Namespace) -> int:
    """Run the detect subcommand."""
    from dbuild import detect
    detect.run(cfg, args)
    return 0


def _dispatch_info(cfg: Config, args: argparse.Namespace) -> int:
    """Run the info subcommand (human-readable detect)."""
    from dbuild import detect
    # Set the format to human so detect.run() uses the pretty printer.
    args.format = "human"
    args.human = True
    detect.run(cfg, args)
    return 0


def _dispatch_ci_run(cfg: Config, args: argparse.Namespace) -> int:
    """Run the ci-run subcommand (full CI pipeline)."""
    from dbuild import ci_run
    rc = ci_run.run(cfg, args)
    return rc if rc else 0


_DISPATCHERS: dict[str, callable] = {
    "build": _dispatch_build,
    "test": _dispatch_test,
    "push": _dispatch_push,
    "sbom": _dispatch_sbom,
    "manifest": _dispatch_manifest,
    "detect": _dispatch_detect,
    "info": _dispatch_info,
    "ci-run": _dispatch_ci_run,
}

# Commands that run without loading project config
_NO_CONFIG_COMMANDS: set[str] = {"init", "ci-prepare", "ci-test-env"}


# ── Entry point ───────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    """Parse arguments, load config, dispatch to subcommand.

    Parameters
    ----------
    argv:
        Argument list for testing.  Defaults to ``sys.argv[1:]``.
    """
    parser = _make_parser()
    args = parser.parse_args(argv)

    # Enable verbose logging
    if args.verbose:
        log.info("verbose mode enabled")

    # If no subcommand was given, print help and exit.
    if args.command is None:
        # If --push was passed without a subcommand, treat it as `build --push`
        if args.push:
            args.command = "build"
        else:
            parser.print_help(sys.stderr)
            sys.exit(2)

    # Commands that don't need project config
    if args.command in _NO_CONFIG_COMMANDS:
        try:
            if args.command == "init":
                from dbuild import init
                rc = init.run(args)
            elif args.command == "ci-prepare":
                from dbuild import prepare
                rc = prepare.run(args)
            elif args.command == "ci-test-env":
                from dbuild import ci_test
                rc = ci_test.run(args)
            else:
                rc = 1
        except KeyboardInterrupt:
            log.warn("interrupted")
            sys.exit(130)
        except Exception as exc:
            log.error(f"{args.command} failed: {exc}")
            if args.verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)
        sys.exit(rc)

    # Load configuration
    try:
        cfg = load_config(Path.cwd())
    except Exception as exc:
        log.error(f"failed to load configuration: {exc}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    # Apply CLI overrides
    cfg = _apply_overrides(cfg, args)

    # Validate: warn if no variants were detected
    if not cfg.variants:
        log.warn("no variants detected -- is this a dbuild project directory?")
        log.warn("expected a Containerfile or .daemonless/config.yaml")

    # Dispatch to subcommand
    dispatcher = _DISPATCHERS.get(args.command)
    if dispatcher is None:
        log.error(f"unknown command: {args.command}")
        sys.exit(2)

    try:
        rc = dispatcher(cfg, args)
    except KeyboardInterrupt:
        log.warn("interrupted")
        sys.exit(130)
    except Exception as exc:
        log.error(f"{args.command} failed: {exc}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    sys.exit(rc)
