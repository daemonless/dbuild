"""Unit tests for dbuild.init."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dbuild.init import _TEMPLATES_DIR, _copy_template, run


class TestCopyTemplate(unittest.TestCase):
    """Tests for _copy_template()."""

    def test_copies_file(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "config.yaml"
            # Only test if template exists
            src = _TEMPLATES_DIR / "config.yaml"
            if not src.exists():
                self.skipTest("config.yaml template not found")
            result = _copy_template("config.yaml", dest)
            self.assertTrue(result)
            self.assertTrue(dest.exists())

    def test_skips_existing(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "config.yaml"
            dest.write_text("existing content")
            result = _copy_template("config.yaml", dest)
            self.assertFalse(result)
            self.assertEqual(dest.read_text(), "existing content")

    def test_missing_template(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "nonexistent.yaml"
            result = _copy_template("nonexistent-template-xyz.yaml", dest)
            self.assertFalse(result)
            self.assertFalse(dest.exists())

    def test_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "sub" / "dir" / "config.yaml"
            src = _TEMPLATES_DIR / "config.yaml"
            if not src.exists():
                self.skipTest("config.yaml template not found")
            result = _copy_template("config.yaml", dest)
            self.assertTrue(result)
            self.assertTrue(dest.parent.exists())


class TestRun(unittest.TestCase):
    """Tests for run()."""

    def test_idempotent(self):
        """Running init twice should skip all files."""
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ):
            args = argparse.Namespace(woodpecker=False, github=False)
            rc1 = run(args)
            rc2 = run(args)
            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)

    def test_returns_zero(self):
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ):
            args = argparse.Namespace(woodpecker=False, github=False)
            rc = run(args)
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
