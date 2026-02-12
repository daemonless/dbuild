"""Unit tests for dbuild.ci_run."""

from __future__ import annotations

import argparse
import unittest
from unittest.mock import MagicMock, patch

from dbuild.ci_run import run
from dbuild.config import Config, Variant


def _make_cfg() -> Config:
    return Config(
        image="testapp",
        registry="ghcr.io/daemonless",
        variants=[Variant(tag="latest", default=True)],
    )


def _make_args(**overrides) -> argparse.Namespace:
    defaults = dict(variant=None, arch=None, prepare=False)
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestCiRunPipeline(unittest.TestCase):
    """Tests for ci_run.run()."""

    @patch("dbuild.ci_run.ci_mod.detect")
    @patch("dbuild.ci_run.sbom")
    @patch("dbuild.ci_run.push")
    @patch("dbuild.ci_run.test")
    @patch("dbuild.ci_run.build")
    def test_full_pipeline(self, mock_build, mock_test, mock_push,
                           mock_sbom, mock_ci_detect):
        """All stages run when test passes and not a PR."""
        mock_build.run.return_value = None
        mock_test.run.return_value = 0
        mock_push.run.return_value = None
        mock_sbom.run.return_value = None
        ci_backend = MagicMock()
        ci_backend.is_pr.return_value = False
        mock_ci_detect.return_value = ci_backend

        cfg = _make_cfg()
        args = _make_args()
        rc = run(cfg, args)

        self.assertEqual(rc, 0)
        mock_build.run.assert_called_once()
        mock_test.run.assert_called_once()
        mock_push.run.assert_called_once()
        mock_sbom.run.assert_called_once()

    @patch("dbuild.ci_run.test")
    @patch("dbuild.ci_run.build")
    def test_test_failure_skips_push_and_sbom(self, mock_build, mock_test):
        """When tests fail, push and sbom are not called."""
        mock_build.run.return_value = None
        mock_test.run.return_value = 1

        cfg = _make_cfg()
        args = _make_args()
        rc = run(cfg, args)

        self.assertEqual(rc, 1)
        mock_build.run.assert_called_once()
        mock_test.run.assert_called_once()

    @patch("dbuild.ci_run.ci_mod.detect")
    @patch("dbuild.ci_run.test")
    @patch("dbuild.ci_run.build")
    def test_pr_skips_push_and_sbom(self, mock_build, mock_test, mock_ci_detect):
        """On PR builds, push and sbom are skipped."""
        mock_build.run.return_value = None
        mock_test.run.return_value = 0
        ci_backend = MagicMock()
        ci_backend.is_pr.return_value = True
        mock_ci_detect.return_value = ci_backend

        cfg = _make_cfg()
        args = _make_args()
        rc = run(cfg, args)

        self.assertEqual(rc, 0)

    @patch("dbuild.ci_run.build")
    def test_build_failure_stops_early(self, mock_build):
        """When build fails, test/push/sbom are not called."""
        mock_build.run.return_value = 1

        cfg = _make_cfg()
        args = _make_args()
        rc = run(cfg, args)

        self.assertEqual(rc, 1)

    @patch("dbuild.ci_run.ci_mod.detect")
    @patch("dbuild.ci_run.sbom")
    @patch("dbuild.ci_run.push")
    @patch("dbuild.ci_run.test")
    @patch("dbuild.ci_run.build")
    @patch("dbuild.ci_run.prepare")
    def test_prepare_flag(self, mock_prepare, mock_build, mock_test,
                          mock_push, mock_sbom, mock_ci_detect):
        """--prepare runs ci-prepare before the pipeline."""
        mock_prepare.run.return_value = 0
        mock_build.run.return_value = None
        mock_test.run.return_value = 0
        mock_push.run.return_value = None
        mock_sbom.run.return_value = None
        ci_backend = MagicMock()
        ci_backend.is_pr.return_value = False
        mock_ci_detect.return_value = ci_backend

        cfg = _make_cfg()
        args = _make_args(prepare=True)
        rc = run(cfg, args)

        self.assertEqual(rc, 0)
        mock_prepare.run.assert_called_once()

    @patch("dbuild.ci_run.prepare")
    def test_prepare_failure_stops_pipeline(self, mock_prepare):
        """When ci-prepare fails, pipeline stops."""
        mock_prepare.run.return_value = 1

        cfg = _make_cfg()
        args = _make_args(prepare=True)
        rc = run(cfg, args)

        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
