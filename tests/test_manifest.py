"""Unit tests for dbuild.manifest."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from dbuild import manifest
from dbuild.manifest import _arch_tag


class TestArchTag(unittest.TestCase):
    """Tests for _arch_tag().

    The suffix must match what `push` produces (config.arch_tag_suffix):
    amd64 is bare, every other arch is ``-<arch>``.
    """

    def test_amd64_no_suffix(self):
        self.assertEqual(_arch_tag("latest", "amd64"), "latest")

    def test_aarch64_suffix(self):
        self.assertEqual(_arch_tag("latest", "aarch64"), "latest-aarch64")

    def test_riscv64_suffix(self):
        self.assertEqual(_arch_tag("latest", "riscv64"), "latest-riscv64")

    def test_pkg_tag(self):
        self.assertEqual(_arch_tag("pkg", "aarch64"), "pkg-aarch64")

    def test_all_known_arches(self):
        for arch in ("amd64", "aarch64", "riscv64"):
            result = _arch_tag("test", arch)
            self.assertTrue(result.startswith("test"))


if __name__ == "__main__":
    unittest.main()


class TestManifestAnnotateIndex(unittest.TestCase):
    """Tests for _manifest_annotate_index()."""

    @patch("dbuild.manifest.subprocess.run")
    @patch("dbuild.manifest.podman._priv_prefix", return_value=[])
    def test_annotation_command(self, _priv, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        manifest._manifest_annotate_index(
            "ghcr.io/daemonless/app:latest",
            {"org.opencontainers.image.description": "My app"},
        )
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[:4], ["podman", "manifest", "annotate", "--index"])
        self.assertIn("org.opencontainers.image.description=My app", cmd)
        self.assertEqual(cmd[-1], "ghcr.io/daemonless/app:latest")

    @patch("dbuild.manifest.subprocess.run")
    @patch("dbuild.manifest.podman._priv_prefix", return_value=[])
    def test_failure_is_nonfatal(self, _priv, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=125, stdout="", stderr="unknown flag: --index"
        )
        # Must not raise
        manifest._manifest_annotate_index("img:latest", {"k": "v"})


class TestImageAvailable(unittest.TestCase):
    """Tests for _image_available().

    Must be remote-only: a local-first check let a persistent host-mode
    runner's stale local images win over the registry, clobbering a
    manifest's amd64 slot with a stale arm64 image (2026-08-07 incident).
    """

    @patch("dbuild.manifest.podman.image_exists")
    @patch("dbuild.manifest.subprocess.run")
    @patch("dbuild.manifest.podman._priv_prefix", return_value=[])
    def test_never_consults_local_store(self, _priv, mock_run, mock_local):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        manifest._image_available("ghcr.io/daemonless/base:15.1")
        mock_local.assert_not_called()

    @patch("dbuild.manifest.subprocess.run")
    @patch("dbuild.manifest.podman._priv_prefix", return_value=[])
    def test_true_when_remote_exists(self, _priv, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        self.assertTrue(manifest._image_available("ghcr.io/daemonless/base:15.1"))

    @patch("dbuild.manifest.subprocess.run")
    @patch("dbuild.manifest.podman._priv_prefix", return_value=[])
    def test_false_when_remote_missing(self, _priv, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="manifest unknown"
        )
        self.assertFalse(manifest._image_available("ghcr.io/daemonless/base:nope"))
