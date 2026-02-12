"""Unit tests for dbuild.prepare."""

from __future__ import annotations

import argparse
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from dbuild import prepare


class TestConfigurePkgRepo(unittest.TestCase):
    """Tests for configure_pkg_repo()."""

    @patch("dbuild.prepare._run")
    @patch("builtins.open", unittest.mock.mock_open())
    @patch("os.makedirs")
    def test_writes_conf_and_updates(self, mock_makedirs, mock_run):
        prepare.configure_pkg_repo()
        mock_makedirs.assert_called_once_with("/etc/pkg", exist_ok=True)
        mock_run.assert_called_once_with(["pkg", "update", "-f"])


class TestInstallPackages(unittest.TestCase):
    """Tests for install_packages()."""

    @patch("dbuild.prepare._run")
    def test_default_packages(self, mock_run):
        prepare.install_packages()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[:3], ["pkg", "install", "-y"])
        self.assertIn("podman", args)
        self.assertIn("trivy", args)
        self.assertNotIn("podman-compose", args)

    @patch("dbuild.prepare._run")
    def test_compose_flag(self, mock_run):
        prepare.install_packages(compose=True)
        args = mock_run.call_args[0][0]
        self.assertIn("podman-compose", args)


class TestInstallOcijail(unittest.TestCase):
    """Tests for install_ocijail()."""

    @patch("dbuild.prepare._run")
    def test_fetches_correct_url(self, mock_run):
        prepare.install_ocijail(arch="amd64")
        calls = mock_run.call_args_list
        # First call: fetch
        fetch_args = calls[0][0][0]
        self.assertEqual(fetch_args[0], "fetch")
        self.assertIn("amd64", fetch_args[-1])
        # Second call: pkg install
        pkg_args = calls[1][0][0]
        self.assertEqual(pkg_args[:3], ["pkg", "install", "-fy"])

    @patch("dbuild.prepare._run")
    def test_aarch64_url(self, mock_run):
        prepare.install_ocijail(arch="aarch64")
        fetch_url = mock_run.call_args_list[0][0][0][-1]
        self.assertIn("aarch64", fetch_url)


class TestCleanupContainers(unittest.TestCase):
    """Tests for cleanup_containers()."""

    @patch("dbuild.prepare._run")
    def test_removes_both_dirs(self, mock_run):
        prepare.cleanup_containers()
        self.assertEqual(mock_run.call_count, 2)
        mock_run.assert_any_call(["rm", "-rf", "/var/db/containers"])
        mock_run.assert_any_call(["rm", "-rf", "/var/lib/containers"])


class TestConfigureNetworking(unittest.TestCase):
    """Tests for configure_networking()."""

    @patch("dbuild.prepare._run")
    def test_kldload_and_sysctl(self, mock_run):
        prepare.configure_networking()
        mock_run.assert_any_call(["kldload", "pf"])
        mock_run.assert_any_call(["sysctl", "net.inet.ip.forwarding=1"])


class TestRun(unittest.TestCase):
    """Tests for prepare.run()."""

    @patch("os.geteuid", return_value=1000)
    def test_not_root_fails(self, _mock_euid):
        args = argparse.Namespace(arch=None, compose=False)
        rc = prepare.run(args)
        self.assertEqual(rc, 1)

    @patch("dbuild.prepare.configure_networking")
    @patch("dbuild.prepare.cleanup_containers")
    @patch("dbuild.prepare.install_ocijail")
    @patch("dbuild.prepare.install_packages")
    @patch("dbuild.prepare.configure_pkg_repo")
    @patch("dbuild.prepare.ci_mod.detect")
    @patch("os.geteuid", return_value=0)
    def test_full_pipeline_in_ci(self, _euid, mock_ci, mock_pkg,
                                 mock_install, mock_ocijail,
                                 mock_cleanup, mock_net):
        """In CI, no confirmation prompt — runs straight through."""
        mock_ci.return_value = MagicMock()  # not LocalCI
        args = argparse.Namespace(arch=None, compose=False)
        rc = prepare.run(args)
        self.assertEqual(rc, 0)
        mock_pkg.assert_called_once()
        mock_install.assert_called_once_with(compose=False)
        mock_ocijail.assert_called_once_with(arch=None)
        mock_cleanup.assert_called_once()
        mock_net.assert_called_once()

    @patch("dbuild.prepare.configure_networking")
    @patch("dbuild.prepare.cleanup_containers")
    @patch("dbuild.prepare.install_ocijail")
    @patch("dbuild.prepare.install_packages")
    @patch("dbuild.prepare.configure_pkg_repo",
           side_effect=subprocess.CalledProcessError(1, "pkg"))
    @patch("dbuild.prepare.ci_mod.detect")
    @patch("os.geteuid", return_value=0)
    def test_command_failure(self, _euid, mock_ci, mock_pkg,
                             mock_install, mock_ocijail,
                             mock_cleanup, mock_net):
        mock_ci.return_value = MagicMock()  # not LocalCI
        args = argparse.Namespace(arch=None, compose=False)
        rc = prepare.run(args)
        self.assertEqual(rc, 1)

    @patch("builtins.input", return_value="y")
    @patch("dbuild.prepare.configure_networking")
    @patch("dbuild.prepare.cleanup_containers")
    @patch("dbuild.prepare.install_ocijail")
    @patch("dbuild.prepare.install_packages")
    @patch("dbuild.prepare.configure_pkg_repo")
    @patch("os.geteuid", return_value=0)
    def test_bare_metal_confirm_yes(self, _euid, mock_pkg, mock_install,
                                    mock_ocijail, mock_cleanup, mock_net,
                                    _mock_input):
        """On bare metal, answering 'y' proceeds normally."""
        args = argparse.Namespace(arch=None, compose=False)
        rc = prepare.run(args)
        self.assertEqual(rc, 0)
        mock_pkg.assert_called_once()

    @patch("builtins.input", return_value="n")
    @patch("os.geteuid", return_value=0)
    def test_bare_metal_confirm_no(self, _euid, _mock_input):
        """On bare metal, answering 'n' aborts."""
        args = argparse.Namespace(arch=None, compose=False)
        rc = prepare.run(args)
        self.assertEqual(rc, 1)

    @patch("builtins.input", side_effect=KeyboardInterrupt)
    @patch("os.geteuid", return_value=0)
    def test_bare_metal_ctrl_c(self, _euid, _mock_input):
        """On bare metal, Ctrl-C aborts."""
        args = argparse.Namespace(arch=None, compose=False)
        rc = prepare.run(args)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
