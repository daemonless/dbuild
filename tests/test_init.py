"""Unit tests for dbuild.init."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dbuild.init import _TEMPLATES_DIR, run


class TestRun(unittest.TestCase):
    """Tests for run()."""

    def test_idempotent(self):
        """Running init twice should skip all files."""
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ):
            args = argparse.Namespace(
                name="testapp",
                title="TestApp",
                category="Apps",
                type="generic",
                port=8080,
                variants="latest",
                dry_run=False,
                woodpecker=False,
                github=False,
            )
            rc1 = run(args)
            rc2 = run(args)
            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)

    def test_returns_zero(self):
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ):
            args = argparse.Namespace(
                name="testapp",
                title="TestApp",
                category="Apps",
                type="generic",
                port=8080,
                variants="latest",
                dry_run=False,
                woodpecker=False,
                github=False,
            )
            rc = run(args)
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
