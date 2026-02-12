"""Unit tests for dbuild.build."""

from __future__ import annotations

import unittest

from dbuild.build import _ARCH_MAP, _map_arch


class TestMapArch(unittest.TestCase):
    """Tests for _map_arch()."""

    def test_amd64(self):
        self.assertEqual(_map_arch("amd64"), "amd64")

    def test_x86_64(self):
        self.assertEqual(_map_arch("x86_64"), "amd64")

    def test_x64(self):
        self.assertEqual(_map_arch("x64"), "amd64")

    def test_arm64(self):
        self.assertEqual(_map_arch("arm64"), "aarch64")

    def test_aarch64(self):
        self.assertEqual(_map_arch("aarch64"), "aarch64")

    def test_riscv64(self):
        self.assertEqual(_map_arch("riscv64"), "riscv64")

    def test_riscv(self):
        self.assertEqual(_map_arch("riscv"), "riscv64")

    def test_unknown_raises(self):
        with self.assertRaises(ValueError) as ctx:
            _map_arch("mips")
        self.assertIn("mips", str(ctx.exception))
        self.assertIn("supported", str(ctx.exception))

    def test_all_map_values(self):
        """Every value in _ARCH_MAP must be one of the canonical architectures."""
        canonical = {"amd64", "aarch64", "riscv64"}
        for val in _ARCH_MAP.values():
            self.assertIn(val, canonical)


if __name__ == "__main__":
    unittest.main()
