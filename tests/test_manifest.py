"""Unit tests for dbuild.manifest."""

from __future__ import annotations

import unittest

from dbuild.manifest import _ARCH_TAG_SUFFIX, _arch_tag


class TestArchTag(unittest.TestCase):
    """Tests for _arch_tag()."""

    def test_amd64_no_suffix(self):
        self.assertEqual(_arch_tag("latest", "amd64"), "latest")

    def test_aarch64_arm64_suffix(self):
        self.assertEqual(_arch_tag("latest", "aarch64"), "latest-arm64")

    def test_arm64_arm64_suffix(self):
        self.assertEqual(_arch_tag("latest", "arm64"), "latest-arm64")

    def test_riscv64_suffix(self):
        self.assertEqual(_arch_tag("latest", "riscv64"), "latest-riscv64")

    def test_pkg_tag(self):
        self.assertEqual(_arch_tag("pkg", "aarch64"), "pkg-arm64")

    def test_unknown_arch_fallback(self):
        result = _arch_tag("latest", "mips")
        self.assertEqual(result, "latest-mips")

    def test_all_known_arches(self):
        for arch in _ARCH_TAG_SUFFIX:
            result = _arch_tag("test", arch)
            self.assertTrue(result.startswith("test"))


if __name__ == "__main__":
    unittest.main()
