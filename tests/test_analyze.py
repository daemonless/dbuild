"""Tests for dbuild.analyze (image bloat and layer audit)."""

from __future__ import annotations

import argparse
import json
import unittest
from unittest.mock import patch

from dbuild.analyze import (
    AnalysisReport,
    BloatItem,
    LayerInfo,
    analyze_image,
    detect_zombie_layers,
    human_size,
    parse_history_json,
    parse_probe_output,
    print_report,
    run_analyze,
)
from dbuild.config import Config, Variant


class TestHumanSize(unittest.TestCase):
    """Test byte formatting utility."""

    def test_bytes(self):
        self.assertEqual(human_size(500), "500 B")

    def test_kilobytes(self):
        self.assertEqual(human_size(2048), "2.0 KB")

    def test_megabytes(self):
        self.assertEqual(human_size(10 * 1024 * 1024), "10.0 MB")

    def test_gigabytes(self):
        self.assertEqual(human_size(int(1.5 * 1024 * 1024 * 1024)), "1.5 GB")


class TestParseHistoryJson(unittest.TestCase):
    """Test parsing podman history JSON."""

    def test_parse_valid_history(self):
        raw = [
            {
                "id": "abc1234567890",
                "size": 150000000,
                "CreatedBy": "/bin/sh -c pkg install -y llvm19",
                "comment": "",
            },
            {
                "id": "<missing>",
                "size": 0,
                "CreatedBy": "/bin/sh -c #(nop) ENV TZ=UTC",
                "comment": "",
            },
        ]
        layers = parse_history_json(raw)
        self.assertEqual(len(layers), 2)
        self.assertEqual(layers[0].id, "abc1234567890")
        self.assertEqual(layers[0].size_bytes, 150000000)
        self.assertFalse(layers[0].is_nop)
        self.assertTrue(layers[1].is_nop)

    def test_parse_empty_history(self):
        self.assertEqual(parse_history_json([]), [])


class TestDetectZombieLayers(unittest.TestCase):
    """Test identifying standalone cleanup layers that fail to reclaim space."""

    def test_detects_standalone_rm_and_pkg_clean(self):
        layers = [
            LayerInfo(
                id="1",
                size_bytes=200000000,
                size_human="200 MB",
                created_by="/bin/sh -c pkg install -y vim",
            ),
            LayerInfo(
                id="2",
                size_bytes=0,
                size_human="0 B",
                created_by="/bin/sh -c rm -rf /var/cache/pkg/*",
            ),
            LayerInfo(
                id="3",
                size_bytes=0,
                size_human="0 B",
                created_by="/bin/sh -c pkg clean -ay",
            ),
        ]
        zombies = detect_zombie_layers(layers)
        self.assertEqual(len(zombies), 2)
        self.assertEqual(zombies[0]["layer_index"], 1)
        self.assertEqual(zombies[1]["layer_index"], 2)
        self.assertIn("copy-on-write", zombies[0]["reason"])

    def test_ignores_combined_run_layers(self):
        layers = [
            LayerInfo(
                id="1",
                size_bytes=200000000,
                size_human="200 MB",
                created_by="/bin/sh -c pkg install -y vim && pkg clean -ay && rm -rf /var/cache/pkg/*",
            ),
        ]
        zombies = detect_zombie_layers(layers)
        self.assertEqual(len(zombies), 0)


class TestParseProbeOutput(unittest.TestCase):
    """Test parsing in-container probe script output into bloat findings."""

    def test_parses_toolchains_static_libs_and_headers(self):
        probe_data = (
            "===PKGS===\n"
            "devel/llvm19\tllvm19\t19.1.7\t750000000\t0\n"
            "devel/git\tgit\t2.48.1\t45000000\t1\n"
            "sysutils/s6\ts6\t2.14.0.1\t2000000\t0\n"
            "===STATIC_LIBS===\n"
            "15000\t/usr/local/lib/libcrypto.a\n"
            "8000\t/usr/local/lib/libssl.a\n"
            "===DIRECTORIES===\n"
            "12000\t/usr/local/include\n"
            "6000\t/usr/local/share/doc\n"
            "4000\t/usr/local/share/man\n"
            "500\t/var/cache/pkg\n"
            "===REPO_SQLITE===\n"
            "200\t/var/db/pkg/repo-FreeBSD.sqlite\n"
            "===END===\n"
        )
        items = parse_probe_output(probe_data, threshold_bytes=1024 * 1024)
        categories = {item.category for item in items}

        self.assertIn("toolchain", categories)
        self.assertIn("static_libs", categories)
        self.assertIn("headers", categories)
        self.assertIn("docs", categories)
        self.assertIn("cache", categories)

        # Check toolchain item details
        toolchain_item = next(i for i in items if i.category == "toolchain")
        self.assertEqual(len(toolchain_item.details), 2)  # llvm19 and git
        self.assertIn("llvm19", toolchain_item.recommendation)

        # Check static libraries item
        static_item = next(i for i in items if i.category == "static_libs")
        self.assertIn("libcrypto.a", static_item.details[0])
        self.assertIn("*.a", static_item.recommendation)


class TestAnalyzeImage(unittest.TestCase):
    """Test analyze_image integration with history and probe execution."""

    @patch("dbuild.analyze.podman.history")
    @patch("dbuild.analyze.podman.run_in")
    def test_analyze_image_success(self, mock_run_in, mock_history):
        mock_history.return_value = [
            {"id": "layer1", "size": 100000000, "CreatedBy": "RUN pkg install -y app"},
            {"id": "layer2", "size": 0, "CreatedBy": "RUN rm -rf /var/cache/pkg"},
        ]
        mock_run_in.return_value = (
            "===PKGS===\n"
            "devel/llvm19\tllvm19\t19.1.7\t20000000\t0\n"
            "===STATIC_LIBS===\n"
            "===DIRECTORIES===\n"
            "===REPO_SQLITE===\n"
            "===END===\n"
        )

        report = analyze_image("test-image:latest", threshold_mb=1.0)
        self.assertEqual(report.image, "test-image:latest")
        self.assertEqual(report.total_size_bytes, 100000000)
        self.assertEqual(len(report.layers), 2)
        self.assertEqual(len(report.zombie_layers), 1)
        self.assertEqual(len(report.bloat_items), 1)
        self.assertTrue(report.rootfs_audit_available)
        self.assertEqual(report.bloat_items[0].category, "toolchain")

    @patch("dbuild.analyze.podman.history")
    @patch("dbuild.analyze.podman.run_in")
    def test_analyze_image_probe_failure_fallback(self, mock_run_in, mock_history):
        mock_history.return_value = [
            {"id": "layer1", "size": 50000000, "CreatedBy": "RUN echo hi"},
        ]
        mock_run_in.side_effect = Exception("Container cannot start (exec format error)")

        report = analyze_image("test-image:foreign", threshold_mb=1.0)
        self.assertEqual(report.total_size_bytes, 50000000)
        self.assertFalse(report.rootfs_audit_available)
        self.assertEqual(len(report.bloat_items), 0)

    @patch("dbuild.analyze.podman.history")
    def test_analyze_image_history_failure(self, mock_history):
        mock_history.side_effect = Exception("Image not found")
        report = analyze_image("missing:image")
        self.assertIsNotNone(report.error_message)
        self.assertIn("Failed to inspect", report.error_message)


class TestPrintReport(unittest.TestCase):
    """Test formatted terminal output."""

    def test_print_report_does_not_crash(self):
        report = AnalysisReport(
            image="localhost/app:latest",
            total_size_bytes=500000000,
            total_size_human="500.0 MB",
            layers=[
                LayerInfo("l1", 300000000, "300.0 MB", "RUN pkg install"),
                LayerInfo("l2", 0, "0 B", "RUN rm -rf /var/cache/pkg"),
            ],
            bloat_items=[
                BloatItem("toolchain", "Compilers", 150000000, "150.0 MB", ["llvm19: 150 MB"], "delete it"),
            ],
            zombie_layers=[{"layer_id": "l2", "command": "RUN rm", "reason": "COW"}],
            potential_savings_bytes=150000000,
            potential_savings_human="150.0 MB",
            rootfs_audit_available=True,
        )
        # Should execute cleanly without error
        print_report(report)


class TestRunAnalyzeCLI(unittest.TestCase):
    """Test CLI handler run_analyze."""

    @patch("dbuild.analyze.analyze_image")
    def test_run_analyze_json_output(self, mock_analyze):
        mock_analyze.return_value = AnalysisReport(
            image="app:latest",
            total_size_bytes=100,
            total_size_human="100 B",
        )
        cfg = Config(image="app", registry="localhost", variants=[Variant(tag="latest")])
        args = argparse.Namespace(
            image="app:latest",
            json_output=True,
            threshold_mb=5.0,
            skip_container=True,
        )

        with patch("builtins.print") as mock_print:
            rc = run_analyze(cfg, args)

        self.assertEqual(rc, 0)
        mock_print.assert_called_once()
        output_str = mock_print.call_args[0][0]
        data = json.loads(output_str)
        self.assertEqual(data["image"], "app:latest")

    @patch("dbuild.analyze.podman.image_exists", return_value=False)
    def test_run_analyze_missing_image_error(self, _mock_exists):
        cfg = Config(image="nonexistent", registry="localhost", variants=[])
        args = argparse.Namespace(
            image=None,
            json_output=False,
            threshold_mb=5.0,
            skip_container=False,
        )
        rc = run_analyze(cfg, args)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
