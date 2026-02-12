"""Unit tests for dbuild.labels."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from dbuild.labels import build_labels


class TestBuildLabels(unittest.TestCase):
    """Tests for build_labels()."""

    @patch("dbuild.labels.subprocess.run")
    def test_created_always_present(self, mock_run):
        mock_run.return_value = type("R", (), {
            "returncode": 1, "stdout": "", "stderr": ""
        })()
        labels = build_labels()
        self.assertIn("org.opencontainers.image.created", labels)

    @patch("dbuild.labels.subprocess.run")
    def test_version_included(self, mock_run):
        mock_run.return_value = type("R", (), {
            "returncode": 1, "stdout": "", "stderr": ""
        })()
        labels = build_labels(version="1.2.3")
        self.assertEqual(labels["org.opencontainers.image.version"], "1.2.3")

    @patch("dbuild.labels.subprocess.run")
    def test_version_omitted_when_none(self, mock_run):
        mock_run.return_value = type("R", (), {
            "returncode": 1, "stdout": "", "stderr": ""
        })()
        labels = build_labels(version=None)
        self.assertNotIn("org.opencontainers.image.version", labels)

    @patch("dbuild.labels.subprocess.run")
    def test_variant_tag_included(self, mock_run):
        mock_run.return_value = type("R", (), {
            "returncode": 1, "stdout": "", "stderr": ""
        })()
        labels = build_labels(variant_tag="pkg")
        self.assertEqual(labels["io.daemonless.variant"], "pkg")

    @patch("dbuild.labels.subprocess.run")
    def test_variant_tag_omitted_when_none(self, mock_run):
        mock_run.return_value = type("R", (), {
            "returncode": 1, "stdout": "", "stderr": ""
        })()
        labels = build_labels(variant_tag=None)
        self.assertNotIn("io.daemonless.variant", labels)

    @patch("dbuild.labels.subprocess.run")
    def test_git_revision_included(self, mock_run):
        mock_run.return_value = type("R", (), {
            "returncode": 0, "stdout": "abc123def456\n", "stderr": ""
        })()
        labels = build_labels()
        self.assertEqual(labels["org.opencontainers.image.revision"], "abc123def456")

    @patch("dbuild.labels.subprocess.run", side_effect=FileNotFoundError)
    def test_git_not_found(self, mock_run):
        """build_labels() should not fail if git is not available."""
        labels = build_labels()
        self.assertNotIn("org.opencontainers.image.revision", labels)
        self.assertIn("org.opencontainers.image.created", labels)

    @patch("dbuild.labels.subprocess.run")
    def test_created_format(self, mock_run):
        mock_run.return_value = type("R", (), {
            "returncode": 1, "stdout": "", "stderr": ""
        })()
        labels = build_labels()
        created = labels["org.opencontainers.image.created"]
        # Should be ISO 8601 format: YYYY-MM-DDTHH:MM:SSZ
        self.assertRegex(created, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


if __name__ == "__main__":
    unittest.main()
