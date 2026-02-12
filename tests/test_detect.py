"""Unit tests for dbuild.detect."""

from __future__ import annotations

import argparse
import unittest

from dbuild.config import Config, TestConfig, Variant
from dbuild.detect import _VM_ARCH_MAP, _build_matrix, _github_extras


def _cfg(**kwargs) -> Config:
    defaults = {
        "image": "testapp",
        "registry": "ghcr.io/daemonless",
        "type": "app",
        "variants": [
            Variant(tag="latest", containerfile="Containerfile", default=True),
            Variant(tag="pkg", containerfile="Containerfile.pkg",
                    args={"BASE_VERSION": "15-quarterly"}),
        ],
        "test": None,
        "architectures": ["amd64"],
    }
    defaults.update(kwargs)
    return Config(**defaults)


def _args(**kwargs) -> argparse.Namespace:
    defaults = {"variant": None, "arch": None}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestBuildMatrix(unittest.TestCase):
    """Tests for _build_matrix()."""

    def test_all_variants(self):
        cfg = _cfg()
        matrix = _build_matrix(cfg, _args())
        self.assertEqual(len(matrix), 2)
        tags = [e["tag"] for e in matrix]
        self.assertEqual(tags, ["latest", "pkg"])

    def test_variant_filter(self):
        cfg = _cfg()
        matrix = _build_matrix(cfg, _args(variant="pkg"))
        self.assertEqual(len(matrix), 1)
        self.assertEqual(matrix[0]["tag"], "pkg")

    def test_arch_filter(self):
        cfg = _cfg(architectures=["amd64", "aarch64"])
        matrix = _build_matrix(cfg, _args(arch="aarch64"))
        self.assertEqual(len(matrix), 2)
        for entry in matrix:
            self.assertEqual(entry["arch"], "aarch64")

    def test_both_filters(self):
        cfg = _cfg(architectures=["amd64", "aarch64"])
        matrix = _build_matrix(cfg, _args(variant="latest", arch="amd64"))
        self.assertEqual(len(matrix), 1)
        self.assertEqual(matrix[0]["tag"], "latest")
        self.assertEqual(matrix[0]["arch"], "amd64")

    def test_no_match(self):
        cfg = _cfg()
        matrix = _build_matrix(cfg, _args(variant="nonexistent"))
        self.assertEqual(matrix, [])

    def test_multi_arch(self):
        cfg = _cfg(
            architectures=["amd64", "aarch64"],
            variants=[Variant(tag="latest", containerfile="Containerfile")],
        )
        matrix = _build_matrix(cfg, _args())
        self.assertEqual(len(matrix), 2)
        arches = [e["arch"] for e in matrix]
        self.assertEqual(arches, ["amd64", "aarch64"])

    def test_matrix_entry_fields(self):
        cfg = _cfg()
        matrix = _build_matrix(cfg, _args())
        entry = matrix[0]
        self.assertIn("tag", entry)
        self.assertIn("containerfile", entry)
        self.assertIn("arch", entry)
        self.assertIn("args", entry)
        self.assertIn("aliases", entry)
        self.assertIn("auto_version", entry)


class TestGithubExtras(unittest.TestCase):
    """Tests for _github_extras()."""

    def test_enrichment(self):
        cfg = _cfg()
        matrix = _build_matrix(cfg, _args())
        enriched, _extras = _github_extras(matrix, cfg)
        self.assertEqual(len(enriched), len(matrix))
        for entry in enriched:
            self.assertIn("type", entry)
            self.assertIn("arch_suffix", entry)
            self.assertIn("vm_arch", entry)
            self.assertIn("vm_sync", entry)

    def test_amd64_suffix_empty(self):
        cfg = _cfg()
        matrix = _build_matrix(cfg, _args())
        enriched, _ = _github_extras(matrix, cfg)
        self.assertEqual(enriched[0]["arch_suffix"], "")
        self.assertEqual(enriched[0]["vm_arch"], "")

    def test_aarch64_suffix(self):
        cfg = _cfg(architectures=["aarch64"],
                    variants=[Variant(tag="latest", containerfile="Containerfile")])
        matrix = _build_matrix(cfg, _args())
        enriched, _ = _github_extras(matrix, cfg)
        self.assertEqual(enriched[0]["arch_suffix"], "-aarch64")
        self.assertEqual(enriched[0]["vm_arch"], "aarch64")

    def test_compose_only_false(self):
        cfg = _cfg()
        matrix = _build_matrix(cfg, _args())
        _, extras = _github_extras(matrix, cfg)
        self.assertEqual(extras["compose_only"], "false")

    def test_compose_only_true(self):
        cfg = _cfg(
            variants=[],
            test=TestConfig(compose=True),
        )
        _, extras = _github_extras([], cfg)
        self.assertEqual(extras["compose_only"], "true")

    def test_manifest_tags(self):
        cfg = _cfg(variants=[
            Variant(tag="latest", aliases=["stable"], containerfile="Containerfile"),
            Variant(tag="pkg", aliases=["stable"], containerfile="Containerfile.pkg"),
        ])
        matrix = _build_matrix(cfg, _args())
        _, extras = _github_extras(matrix, cfg)
        # "stable" appears in both variants but should be deduped
        self.assertEqual(extras["manifest_tags"], "latest stable pkg")


class TestVmArchMap(unittest.TestCase):
    """Tests for _VM_ARCH_MAP lookups."""

    def test_known_architectures(self):
        for arch in ("amd64", "aarch64", "riscv64"):
            self.assertIn(arch, _VM_ARCH_MAP)

    def test_amd64_no_suffix(self):
        self.assertEqual(_VM_ARCH_MAP["amd64"]["arch_suffix"], "")

    def test_riscv64_scp_sync(self):
        self.assertEqual(_VM_ARCH_MAP["riscv64"]["vm_sync"], "scp")


if __name__ == "__main__":
    unittest.main()
