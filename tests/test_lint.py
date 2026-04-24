"""Unit tests for dbuild.lint."""

from __future__ import annotations

import unittest
from pathlib import Path

from dbuild.lint import lint_repo


def _make_repo(tmp_path: Path, config_yaml: str) -> Path:
    (tmp_path / "Containerfile").write_text("FROM scratch\n")
    daemonless = tmp_path / ".daemonless"
    daemonless.mkdir()
    (daemonless / "config.yaml").write_text(config_yaml)
    return tmp_path


class TestPkgNameRedundancy(unittest.TestCase):
    """Lint warns when pkg_name and args.PKG_NAME are both set."""

    def test_redundant_same_value_warns(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d), (
                "build:\n"
                "  variants:\n"
                "    - tag: lts\n"
                "      pkg_name: forgejo-lts\n"
                "      args:\n"
                "        PKG_NAME: forgejo-lts\n"
            ))
            _, warnings = lint_repo(repo)
        self.assertTrue(
            any("redundant" in w for w in warnings),
            f"Expected redundancy warning, got: {warnings}",
        )

    def test_conflicting_values_warns(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d), (
                "build:\n"
                "  variants:\n"
                "    - tag: lts\n"
                "      pkg_name: forgejo-lts\n"
                "      args:\n"
                "        PKG_NAME: wrong-package\n"
            ))
            _, warnings = lint_repo(repo)
        self.assertTrue(
            any("differs" in w for w in warnings),
            f"Expected conflict warning, got: {warnings}",
        )

    def test_only_pkg_name_no_warn(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d), (
                "build:\n"
                "  variants:\n"
                "    - tag: lts\n"
                "      pkg_name: forgejo-lts\n"
            ))
            _, warnings = lint_repo(repo)
        pkg_warns = [w for w in warnings if "PKG_NAME" in w or "pkg_name" in w]
        self.assertEqual(pkg_warns, [])

    def test_only_arg_no_warn(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d), (
                "build:\n"
                "  variants:\n"
                "    - tag: latest\n"
                "      args:\n"
                "        PKG_NAME: forgejo\n"
            ))
            _, warnings = lint_repo(repo)
        pkg_warns = [w for w in warnings if "redundant" in w or "differs" in w]
        self.assertEqual(pkg_warns, [])


if __name__ == "__main__":
    unittest.main()
