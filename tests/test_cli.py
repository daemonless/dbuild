"""Unit tests for dbuild.cli."""

from __future__ import annotations

import argparse
import unittest

from dbuild.cli import _apply_overrides, _make_parser
from dbuild.config import Config


class TestMakeParser(unittest.TestCase):
    """Tests for _make_parser()."""

    def setUp(self):
        self.parser = _make_parser()

    def test_version_flag(self):
        with self.assertRaises(SystemExit) as ctx:
            self.parser.parse_args(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_no_args_no_command(self):
        args = self.parser.parse_args([])
        self.assertIsNone(args.command)

    def test_build_command(self):
        args = self.parser.parse_args(["build"])
        self.assertEqual(args.command, "build")

    def test_test_command(self):
        args = self.parser.parse_args(["test"])
        self.assertEqual(args.command, "test")

    def test_push_command(self):
        args = self.parser.parse_args(["push"])
        self.assertEqual(args.command, "push")

    def test_detect_command(self):
        args = self.parser.parse_args(["detect"])
        self.assertEqual(args.command, "detect")

    def test_info_command(self):
        args = self.parser.parse_args(["info"])
        self.assertEqual(args.command, "info")

    def test_init_command(self):
        args = self.parser.parse_args(["init"])
        self.assertEqual(args.command, "init")

    def test_sbom_command(self):
        args = self.parser.parse_args(["sbom"])
        self.assertEqual(args.command, "sbom")

    def test_manifest_command(self):
        args = self.parser.parse_args(["manifest"])
        self.assertEqual(args.command, "manifest")

    def test_ci_test_env_command(self):
        args = self.parser.parse_args(["ci-test-env"])
        self.assertEqual(args.command, "ci-test-env")

    def test_ci_prepare_command(self):
        args = self.parser.parse_args(["ci-prepare"])
        self.assertEqual(args.command, "ci-prepare")

    def test_ci_run_command(self):
        args = self.parser.parse_args(["ci-run"])
        self.assertEqual(args.command, "ci-run")

    def test_ci_prepare_arch(self):
        args = self.parser.parse_args(["ci-prepare", "--arch", "aarch64"])
        self.assertEqual(args.arch, "aarch64")

    def test_ci_prepare_compose(self):
        args = self.parser.parse_args(["ci-prepare", "--compose"])
        self.assertTrue(args.compose)

    def test_ci_run_variant(self):
        args = self.parser.parse_args(["ci-run", "--variant", "pkg"])
        self.assertEqual(args.variant, "pkg")

    def test_ci_run_arch(self):
        args = self.parser.parse_args(["ci-run", "--arch", "amd64"])
        self.assertEqual(args.arch, "amd64")

    def test_ci_run_prepare_flag(self):
        args = self.parser.parse_args(["ci-run", "--prepare"])
        self.assertTrue(args.prepare)

    # ── Global options ────────────────────────────────────────────────

    def test_global_variant(self):
        args = self.parser.parse_args(["--variant", "pkg", "build"])
        self.assertEqual(args.variant, "pkg")

    def test_global_arch(self):
        args = self.parser.parse_args(["--arch", "aarch64", "build"])
        self.assertEqual(args.arch, "aarch64")

    def test_global_registry(self):
        args = self.parser.parse_args(["--registry", "myregistry.io/org", "build"])
        self.assertEqual(args.registry, "myregistry.io/org")

    def test_global_verbose(self):
        args = self.parser.parse_args(["-v", "build"])
        self.assertTrue(args.verbose)

    def test_global_push(self):
        args = self.parser.parse_args(["--push", "build"])
        self.assertTrue(args.push)

    # ── Subcommand-level --variant and --arch ─────────────────────────

    def test_build_variant(self):
        args = self.parser.parse_args(["build", "--variant", "pkg"])
        self.assertEqual(args.variant, "pkg")

    def test_build_arch(self):
        args = self.parser.parse_args(["build", "--arch", "amd64"])
        self.assertEqual(args.arch, "amd64")

    def test_push_variant(self):
        args = self.parser.parse_args(["push", "--variant", "latest"])
        self.assertEqual(args.variant, "latest")

    def test_push_arch(self):
        args = self.parser.parse_args(["push", "--arch", "riscv64"])
        self.assertEqual(args.arch, "riscv64")

    def test_test_variant(self):
        args = self.parser.parse_args(["test", "--variant", "pkg"])
        self.assertEqual(args.variant, "pkg")

    def test_sbom_variant(self):
        args = self.parser.parse_args(["sbom", "--variant", "latest"])
        self.assertEqual(args.variant, "latest")

    def test_sbom_arch(self):
        args = self.parser.parse_args(["sbom", "--arch", "aarch64"])
        self.assertEqual(args.arch, "aarch64")

    # ── SUPPRESS behavior ─────────────────────────────────────────────

    def test_subcommand_variant_suppress_default(self):
        """Subcommand --variant uses SUPPRESS, so it shouldn't overwrite global."""
        args = self.parser.parse_args(["--variant", "pkg", "build"])
        self.assertEqual(args.variant, "pkg")

    def test_subcommand_variant_overrides_global(self):
        """Subcommand --variant takes precedence when provided."""
        args = self.parser.parse_args(["--variant", "pkg", "build", "--variant", "latest"])
        self.assertEqual(args.variant, "latest")

    # ── Detect format ────────────────────────────────────────────────

    def test_detect_format_default(self):
        args = self.parser.parse_args(["detect"])
        self.assertEqual(args.format, "json")

    def test_detect_format_github(self):
        args = self.parser.parse_args(["detect", "--format", "github"])
        self.assertEqual(args.format, "github")

    # ── Init options ──────────────────────────────────────────────────

    def test_init_github_flag(self):
        args = self.parser.parse_args(["init", "--github"])
        self.assertTrue(args.github)

    def test_init_woodpecker_flag(self):
        args = self.parser.parse_args(["init", "--woodpecker"])
        self.assertTrue(args.woodpecker)


class TestApplyOverrides(unittest.TestCase):
    """Tests for _apply_overrides()."""

    def test_registry_override(self):
        cfg = Config(image="test", registry="ghcr.io/daemonless")
        args = argparse.Namespace(registry="myregistry.io/org", arch=None)
        cfg = _apply_overrides(cfg, args)
        self.assertEqual(cfg.registry, "myregistry.io/org")

    def test_arch_override(self):
        cfg = Config(image="test", registry="ghcr.io/daemonless",
                     architectures=["amd64", "aarch64"])
        args = argparse.Namespace(registry=None, arch="riscv64")
        cfg = _apply_overrides(cfg, args)
        self.assertEqual(cfg.architectures, ["riscv64"])

    def test_no_overrides(self):
        cfg = Config(image="test", registry="ghcr.io/daemonless")
        args = argparse.Namespace(registry=None, arch=None)
        cfg = _apply_overrides(cfg, args)
        self.assertEqual(cfg.registry, "ghcr.io/daemonless")
        self.assertEqual(cfg.architectures, ["amd64"])


if __name__ == "__main__":
    unittest.main()
