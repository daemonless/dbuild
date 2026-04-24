"""Unit tests for dbuild.build."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from dbuild.build import _ARCH_MAP, _build_variant, _map_arch
from dbuild.config import Variant


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


class TestPkgNameBuildArg(unittest.TestCase):
    """pkg_name on a variant is auto-injected as PKG_NAME build arg."""

    def _run_build_variant(self, variant: Variant) -> dict:
        """Run build_variant with all side-effects mocked; return captured build_args."""
        captured: dict = {}

        def fake_build(containerfile, tag, *, build_args=None, **kw):
            captured.update(build_args or {})
            return tag

        cfg = MagicMock()
        cfg.full_image = "ghcr.io/daemonless/testapp"

        with patch("dbuild.build.podman.build", side_effect=fake_build), \
             patch("dbuild.build.podman.run_in", return_value=""), \
             patch("dbuild.build.version.extract_version", return_value="1.0"), \
             patch("dbuild.build.labels.build_labels", return_value={}), \
             patch("dbuild.build.podman.bah_from", return_value="ctr"), \
             patch("dbuild.build.podman.bah_config"), \
             patch("dbuild.build.podman.bah_commit", return_value="sha"), \
             patch("dbuild.build.podman.bah_rm"), \
             patch("dbuild.build.log.step"), \
             patch("dbuild.build.log.info"), \
             patch("dbuild.build.log.timer_start"), \
             patch("dbuild.build.log.timer_stop"), \
             patch("dbuild.build.os.environ.get", return_value=None):
            _build_variant(cfg, variant, arch="amd64")
        return captured

    def test_pkg_name_injected(self):
        v = Variant(tag="latest", containerfile="Containerfile", pkg_name="forgejo-lts")
        args = self._run_build_variant(v)
        self.assertEqual(args.get("PKG_NAME"), "forgejo-lts")

    def test_pkg_name_not_overridden_by_explicit_arg(self):
        v = Variant(tag="latest", containerfile="Containerfile",
                    pkg_name="forgejo-lts", args={"PKG_NAME": "custom"})
        args = self._run_build_variant(v)
        self.assertEqual(args.get("PKG_NAME"), "custom")

    def test_pkg_name_absent_when_none(self):
        v = Variant(tag="latest", containerfile="Containerfile", pkg_name=None)
        args = self._run_build_variant(v)
        self.assertNotIn("PKG_NAME", args)


if __name__ == "__main__":
    unittest.main()
