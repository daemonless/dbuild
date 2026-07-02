"""Unit tests for dbuild.push."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from dbuild.config import Variant
from dbuild.push import _collect_tags, _local_tag_variant


class TestCollectTags(unittest.TestCase):
    """Tests for _collect_tags()."""

    def test_no_aliases(self):
        v = Variant(tag="latest")
        self.assertEqual(_collect_tags(v, "amd64"), ["latest"])

    def test_with_aliases(self):
        v = Variant(tag="latest", aliases=["stable", "15"])
        self.assertEqual(_collect_tags(v, "amd64"), ["latest", "stable", "15"])

    def test_alias_dedup(self):
        """Alias that matches primary tag should not duplicate."""
        v = Variant(tag="latest", aliases=["latest", "stable"])
        tags = _collect_tags(v, "amd64")
        self.assertEqual(tags.count("latest"), 1)
        self.assertEqual(tags, ["latest", "stable"])

    def test_order_preserved(self):
        v = Variant(tag="pkg", aliases=["quarterly", "15-quarterly"])
        tags = _collect_tags(v, "amd64")
        self.assertEqual(tags, ["pkg", "quarterly", "15-quarterly"])

    def test_non_amd64_suffixes_all_tags(self):
        v = Variant(tag="pkg", aliases=["quarterly"])
        tags = _collect_tags(v, "aarch64", "2.0")
        self.assertEqual(tags, ["pkg-aarch64", "quarterly-aarch64", "2.0-pkg-aarch64"])


class TestLocalTagVariant(unittest.TestCase):
    """Tests for local build-tag promotion helper."""

    def test_missing_build_image_raises(self):
        cfg = MagicMock()
        cfg.full_image = "ghcr.io/daemonless/testapp"
        variant = Variant(tag="latest")

        with (
            patch("dbuild.push.podman.image_exists", return_value=False),
            self.assertRaises(RuntimeError),
        ):
            _local_tag_variant(cfg, variant, "amd64")

    def test_tags_primary_alias_and_version(self):
        cfg = MagicMock()
        cfg.full_image = "ghcr.io/daemonless/testapp"
        variant = Variant(tag="pkg", aliases=["quarterly"])

        with patch("dbuild.push.podman.image_exists", return_value=True), \
             patch("dbuild.push.podman.inspect_labels", return_value={
                 "org.opencontainers.image.version": "2.0",
             }), \
             patch("dbuild.push.podman.tag") as mock_tag:
            tags = _local_tag_variant(cfg, variant, "aarch64")

        self.assertEqual(tags, ["pkg-aarch64", "quarterly-aarch64", "2.0-pkg-aarch64"])
        mock_tag.assert_any_call(
            "ghcr.io/daemonless/testapp:build-pkg",
            "ghcr.io/daemonless/testapp:pkg-aarch64",
        )
        mock_tag.assert_any_call(
            "ghcr.io/daemonless/testapp:build-pkg",
            "ghcr.io/daemonless/testapp:quarterly-aarch64",
        )
        mock_tag.assert_any_call(
            "ghcr.io/daemonless/testapp:build-pkg",
            "ghcr.io/daemonless/testapp:2.0-pkg-aarch64",
        )
        self.assertEqual(mock_tag.call_count, 3)


if __name__ == "__main__":
    unittest.main()
