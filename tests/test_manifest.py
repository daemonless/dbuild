"""Unit tests for dbuild.manifest."""

from __future__ import annotations

import unittest

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
