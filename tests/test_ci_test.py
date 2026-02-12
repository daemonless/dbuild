"""Unit tests for dbuild.ci_test."""

from __future__ import annotations

import argparse
import unittest
from unittest.mock import MagicMock, patch

from dbuild import ci_test


class TestCheckTool(unittest.TestCase):
    """Tests for _check_tool()."""

    @patch("shutil.which", return_value="/usr/local/bin/podman")
    def test_found(self, _mock):
        self.assertTrue(ci_test._check_tool("podman"))

    @patch("shutil.which", return_value=None)
    def test_not_found(self, _mock):
        self.assertFalse(ci_test._check_tool("podman"))


class TestCheckPodmanInfo(unittest.TestCase):
    """Tests for _check_podman_info()."""

    @patch("subprocess.run")
    def test_ocijail_runtime(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ocijail\n")
        self.assertTrue(ci_test._check_podman_info())

    @patch("subprocess.run")
    def test_other_runtime(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="runc\n")
        self.assertTrue(ci_test._check_podman_info())

    @patch("subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        self.assertFalse(ci_test._check_podman_info())

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_no_podman(self, _mock):
        self.assertFalse(ci_test._check_podman_info())


class TestCheckIpForwarding(unittest.TestCase):
    """Tests for _check_ip_forwarding()."""

    @patch("subprocess.run")
    def test_enabled(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="1\n")
        self.assertTrue(ci_test._check_ip_forwarding())

    @patch("subprocess.run")
    def test_disabled(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="0\n")
        self.assertFalse(ci_test._check_ip_forwarding())


class TestCheckPfLoaded(unittest.TestCase):
    """Tests for _check_pf_loaded()."""

    @patch("subprocess.run")
    def test_loaded(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(ci_test._check_pf_loaded())

    @patch("subprocess.run")
    def test_not_loaded(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        self.assertFalse(ci_test._check_pf_loaded())


class TestCheckOcijailAnnotation(unittest.TestCase):
    """Tests for _check_ocijail_annotation()."""

    @patch("subprocess.run")
    @patch("dbuild.podman._priv_prefix", return_value=[])
    def test_annotation_supported(self, _priv, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
        self.assertTrue(
            ci_test._check_ocijail_annotation(
                "org.freebsd.jail.allow.mlock", "base:latest",
            )
        )

    @patch("subprocess.run")
    @patch("dbuild.podman._priv_prefix", return_value=[])
    def test_annotation_unsupported(self, _priv, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        self.assertFalse(
            ci_test._check_ocijail_annotation(
                "org.freebsd.jail.allow.mlock", "base:latest",
            )
        )


class TestFindBaseImage(unittest.TestCase):
    """Tests for _find_base_image()."""

    @patch("subprocess.run")
    @patch("dbuild.podman._priv_prefix", return_value=[])
    def test_finds_freebsd_image(self, _priv, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="ghcr.io/daemonless/freebsd:15\n",
        )
        self.assertEqual(ci_test._find_base_image(), "ghcr.io/daemonless/freebsd:15")

    @patch("subprocess.run")
    @patch("dbuild.podman._priv_prefix", return_value=[])
    def test_no_images(self, _priv, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        self.assertIsNone(ci_test._find_base_image())


class TestRun(unittest.TestCase):
    """Tests for ci_test.run()."""

    @patch("dbuild.ci_test._check_ci_env")
    @patch("dbuild.ci_test._check_ocijail_annotations", return_value=(2, 0))
    @patch("dbuild.ci_test._check_ip_forwarding", return_value=True)
    @patch("dbuild.ci_test._check_pf_loaded", return_value=True)
    @patch("dbuild.ci_test._check_podman_info", return_value=True)
    @patch("dbuild.ci_test._check_tool", return_value=True)
    def test_all_pass(self, *_mocks):
        args = argparse.Namespace()
        self.assertEqual(ci_test.run(args), 0)

    @patch("dbuild.ci_test._check_ci_env")
    @patch("dbuild.ci_test._check_ocijail_annotations", return_value=(2, 0))
    @patch("dbuild.ci_test._check_ip_forwarding", return_value=True)
    @patch("dbuild.ci_test._check_pf_loaded", return_value=True)
    @patch("dbuild.ci_test._check_podman_info", return_value=True)
    @patch("dbuild.ci_test._check_tool", return_value=False)
    def test_missing_tool_fails(self, *_mocks):
        args = argparse.Namespace()
        self.assertEqual(ci_test.run(args), 1)

    @patch("dbuild.ci_test._check_ci_env")
    @patch("dbuild.ci_test._check_ocijail_annotations", return_value=(2, 0))
    @patch("dbuild.ci_test._check_ip_forwarding", return_value=True)
    @patch("dbuild.ci_test._check_pf_loaded", return_value=False)
    @patch("dbuild.ci_test._check_podman_info", return_value=True)
    @patch("dbuild.ci_test._check_tool", return_value=True)
    def test_pf_not_loaded_fails(self, *_mocks):
        args = argparse.Namespace()
        self.assertEqual(ci_test.run(args), 1)

    @patch("dbuild.ci_test._check_ci_env")
    @patch("dbuild.ci_test._check_ocijail_annotations", return_value=(0, 2))
    @patch("dbuild.ci_test._check_ip_forwarding", return_value=True)
    @patch("dbuild.ci_test._check_pf_loaded", return_value=True)
    @patch("dbuild.ci_test._check_podman_info", return_value=True)
    @patch("dbuild.ci_test._check_tool", return_value=True)
    def test_annotation_warnings_still_pass(self, *_mocks):
        """Unsupported annotations warn but don't fail the check."""
        args = argparse.Namespace()
        self.assertEqual(ci_test.run(args), 0)


if __name__ == "__main__":
    unittest.main()
