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
