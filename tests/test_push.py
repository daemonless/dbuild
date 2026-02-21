"""Unit tests for dbuild.push."""

from __future__ import annotations

import unittest

from dbuild.config import Variant
from dbuild.push import _collect_tags


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


if __name__ == "__main__":
    unittest.main()
