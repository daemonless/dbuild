"""Unit tests for dbuild.init."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dbuild.init import _build_variants_block, run


def _base_args(**overrides):
    args = argparse.Namespace(
        name="testapp",
        title="TestApp",
        category="Apps",
        type="generic",
        port=8080,
        variants="latest",
        community=None,
        dry_run=False,
        woodpecker=False,
        github=False,
        freebsd_port=None,
        flavors=None,
    )
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


class TestRun(unittest.TestCase):
    """Tests for run()."""

    def test_idempotent(self):
        """Running init twice should skip all files."""
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ):
            args = _base_args()
            rc1 = run(args)
            rc2 = run(args)
            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)

    def test_returns_zero(self):
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ):
            args = _base_args()
            rc = run(args)
            self.assertEqual(rc, 0)

    def test_config_yaml_has_no_unrendered_placeholder(self):
        """Regression: variants_block must actually be substituted, not left
        as a literal `{{ variants_block }}` placeholder in the output."""
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ):
            args = _base_args(variants="latest,pkg,pkg-latest")
            run(args)
            content = (Path(d) / ".daemonless" / "config.yaml").read_text()
        self.assertNotIn("variants_block", content)
        self.assertIn("- tag: latest", content)
        self.assertIn("- tag: pkg", content)
        self.assertIn("- tag: pkg-latest", content)
        self.assertIn('BASE_VERSION: "15.1-latest"', content)

    def test_scaffolds_ready_signal_files(self):
        """run.sh uses s6-ready-when, so notification-fd must exist with '3'."""
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ):
            run(_base_args())
            svc = Path(d) / "root" / "etc" / "services.d" / "testapp"
            self.assertEqual((svc / "notification-fd").read_text().strip(), "3")
            run_text = (svc / "run").read_text()
            healthz = (Path(d) / "root" / "healthz").read_text()
        self.assertIn("s6-ready-when /healthz", run_text)
        # exec must continue onto the arg lines, not drop them
        self.assertIn("exec /usr/local/bin/s6-setuidgid bsd \\\n", run_text)
        # cloudflared leftovers must not leak into generic scaffolds
        self.assertNotIn("TUNNEL_TOKEN", run_text)
        self.assertNotIn("TUNNEL_TOKEN", healthz)


class TestFlavors(unittest.TestCase):
    """Tests for --flavors validation and wiring in run()."""

    def test_flavors_without_freebsd_port_errors(self):
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ):
            args = _base_args(flavors="lua,wolfssl")
            rc = run(args)
        self.assertEqual(rc, 1)
        self.assertFalse((Path(d) / ".daemonless").exists())

    def test_unresolvable_flavor_aborts_without_scaffolding(self):
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ), patch("dbuild.init._fetch_port_metadata", return_value=None):
            args = _base_args(freebsd_port="editors/joe", flavors="bogus")
            rc = run(args)
        self.assertEqual(rc, 1)
        self.assertFalse((Path(d) / ".daemonless").exists())

    def test_flavors_cross_into_variants_block(self):
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ), patch(
            "dbuild.init._fetch_port_metadata",
            return_value={
                "name": "joe", "pkgname": "joe",
                "flavor_pkgnames": {"tiny": "joe", "x11": "joe-x11"},
            },
        ):
            args = _base_args(
                freebsd_port="editors/joe", variants="latest,pkg,pkg-latest",
                flavors="tiny,x11",
            )
            rc = run(args)
            content = (Path(d) / ".daemonless" / "config.yaml").read_text()
        self.assertEqual(rc, 0)
        self.assertIn("- tag: latest", content)
        self.assertNotIn("- tag: pkg\n", content)  # replaced by flavor tags
        self.assertIn("- tag: pkg-tiny", content)
        self.assertIn("pkg_name: joe-x11", content)
        self.assertIn("- tag: pkg-latest-tiny", content)
        self.assertIn("- tag: pkg-latest-x11", content)


class TestBuildVariantsBlock(unittest.TestCase):
    """Tests for _build_variants_block() in isolation."""

    def test_no_flavors_matches_legacy_template(self):
        block = _build_variants_block(["latest", "pkg", "pkg-latest"], {})
        self.assertEqual(block, (
            "    - tag: latest\n"
            "      containerfile: Containerfile\n"
            "    - tag: pkg\n"
            "      containerfile: Containerfile.pkg\n"
            "    - tag: pkg-latest\n"
            "      containerfile: Containerfile.pkg\n"
            '      args:\n'
            '        BASE_VERSION: "15.1-latest"'
        ))

    def test_flavors_only_cross_pkg_tags(self):
        block = _build_variants_block(["latest"], {"lua": "app-lua"})
        self.assertEqual(block, (
            "    - tag: latest\n"
            "      containerfile: Containerfile"
        ))

    def test_flavors_cross_pkg_latest_suffix_preserved(self):
        block = _build_variants_block(["pkg-latest"], {"lua": "app-lua"})
        self.assertIn("- tag: pkg-latest-lua", block)
        self.assertIn("pkg_name: app-lua", block)
        self.assertIn('BASE_VERSION: "15.1-latest"', block)


if __name__ == "__main__":
    unittest.main()
