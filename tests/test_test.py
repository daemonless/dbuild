"""Unit tests for dbuild.test (CIT) functions."""

from __future__ import annotations

import http.server
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dbuild import test as cit


class TestReadLabels(unittest.TestCase):
    """Tests for _read_labels()."""

    @patch("dbuild.test.podman.inspect_labels")
    def test_extracts_port_and_health(self, mock_labels):
        mock_labels.return_value = {
            "io.daemonless.port": "7878",
            "io.daemonless.healthcheck-url": "http://localhost:7878/ping",
        }
        result = cit._read_labels("myimage:latest")
        self.assertEqual(result["port"], 7878)
        self.assertEqual(result["health"], "/ping")
        self.assertEqual(result["jail_annotations"], {})

    @patch("dbuild.test.podman.inspect_labels")
    def test_health_url_path_only(self, mock_labels):
        mock_labels.return_value = {
            "io.daemonless.healthcheck-url": "/api/health",
        }
        result = cit._read_labels("myimage:latest")
        self.assertEqual(result["health"], "/api/health")

    @patch("dbuild.test.podman.inspect_labels")
    def test_health_url_root(self, mock_labels):
        mock_labels.return_value = {
            "io.daemonless.healthcheck-url": "http://localhost:8080",
        }
        result = cit._read_labels("myimage:latest")
        self.assertEqual(result["health"], "/")

    @patch("dbuild.test.podman.inspect_labels")
    def test_no_value_ignored(self, mock_labels):
        mock_labels.return_value = {
            "io.daemonless.port": "<no value>",
            "io.daemonless.healthcheck-url": "<no value>",
        }
        result = cit._read_labels("myimage:latest")
        self.assertIsNone(result["port"])
        self.assertIsNone(result["health"])

    @patch("dbuild.test.podman.inspect_labels")
    def test_invalid_port_ignored(self, mock_labels):
        mock_labels.return_value = {"io.daemonless.port": "notanumber"}
        result = cit._read_labels("myimage:latest")
        self.assertIsNone(result["port"])

    @patch("dbuild.test.podman.inspect_labels")
    def test_jail_annotations_extracted(self, mock_labels):
        mock_labels.return_value = {
            "org.freebsd.jail.allow.mlock": "required",
            "org.freebsd.jail.allow.sysvipc": "true",
            "org.freebsd.jail.allow.raw_sockets": "false",
            "io.daemonless.port": "5432",
        }
        result = cit._read_labels("myimage:latest")
        self.assertEqual(result["jail_annotations"], {
            "org.freebsd.jail.allow.mlock": "true",
            "org.freebsd.jail.allow.sysvipc": "true",
        })
        # "false" value should NOT be included
        self.assertNotIn("org.freebsd.jail.allow.raw_sockets", result["jail_annotations"])

    @patch("dbuild.test.podman.inspect_labels")
    def test_empty_labels(self, mock_labels):
        mock_labels.return_value = {}
        result = cit._read_labels("myimage:latest")
        self.assertIsNone(result["port"])
        self.assertIsNone(result["health"])
        self.assertEqual(result["jail_annotations"], {})


class TestFindBaseline(unittest.TestCase):
    """Tests for _find_baseline()."""

    def test_no_baseline(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(cit._find_baseline(Path(d)))

    def test_default_baseline(self):
        with tempfile.TemporaryDirectory() as d:
            bl = Path(d) / ".daemonless" / "baseline.png"
            bl.parent.mkdir(parents=True)
            bl.touch()
            self.assertEqual(cit._find_baseline(Path(d)), bl)

    def test_baselines_dir(self):
        with tempfile.TemporaryDirectory() as d:
            bl = Path(d) / ".daemonless" / "baselines" / "baseline.png"
            bl.parent.mkdir(parents=True)
            bl.touch()
            self.assertEqual(cit._find_baseline(Path(d)), bl)

    def test_tag_specific_preferred(self):
        with tempfile.TemporaryDirectory() as d:
            default = Path(d) / ".daemonless" / "baseline.png"
            default.parent.mkdir(parents=True)
            default.touch()
            tagged = Path(d) / ".daemonless" / "baseline-pkg.png"
            tagged.touch()
            self.assertEqual(cit._find_baseline(Path(d), "pkg"), tagged)

    def test_tag_specific_in_baselines_dir(self):
        with tempfile.TemporaryDirectory() as d:
            bl = Path(d) / ".daemonless" / "baselines" / "baseline-latest.png"
            bl.parent.mkdir(parents=True)
            bl.touch()
            self.assertEqual(cit._find_baseline(Path(d), "latest"), bl)

    def test_falls_back_to_default_when_tag_missing(self):
        with tempfile.TemporaryDirectory() as d:
            default = Path(d) / ".daemonless" / "baseline.png"
            default.parent.mkdir(parents=True)
            default.touch()
            self.assertEqual(cit._find_baseline(Path(d), "nonexistent"), default)


class TestFindComposeFile(unittest.TestCase):
    """Tests for _find_compose_file()."""

    def test_no_compose(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(cit._find_compose_file(Path(d)))

    def test_compose_yaml(self):
        with tempfile.TemporaryDirectory() as d:
            cf = Path(d) / ".daemonless" / "compose.yaml"
            cf.parent.mkdir(parents=True)
            cf.touch()
            self.assertEqual(cit._find_compose_file(Path(d)), cf)

    def test_compose_yml(self):
        with tempfile.TemporaryDirectory() as d:
            cf = Path(d) / ".daemonless" / "compose.yml"
            cf.parent.mkdir(parents=True)
            cf.touch()
            self.assertEqual(cit._find_compose_file(Path(d)), cf)

    def test_yaml_preferred_over_yml(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, ".daemonless").mkdir(parents=True)
            (Path(d) / ".daemonless" / "compose.yaml").touch()
            (Path(d) / ".daemonless" / "compose.yml").touch()
            result = cit._find_compose_file(Path(d))
            self.assertTrue(result.name == "compose.yaml")


class TestDowngradeMode(unittest.TestCase):
    """Tests for _downgrade_mode()."""

    def test_screenshot_to_health_with_health(self):
        result = cit._downgrade_mode("screenshot", port=8080, health="/api")
        self.assertEqual(result, "health")

    def test_screenshot_to_health_with_port_only(self):
        result = cit._downgrade_mode("screenshot", port=8080, health=None)
        self.assertEqual(result, "health")

    def test_screenshot_to_shell_with_nothing(self):
        result = cit._downgrade_mode("screenshot", port=None, health=None)
        self.assertEqual(result, "shell")

    def test_health_unchanged(self):
        result = cit._downgrade_mode("health", port=8080, health="/")
        self.assertEqual(result, "health")

    def test_port_unchanged(self):
        result = cit._downgrade_mode("port", port=8080, health=None)
        self.assertEqual(result, "port")

    def test_shell_unchanged(self):
        result = cit._downgrade_mode("shell", port=None, health=None)
        self.assertEqual(result, "shell")


class TestResolveMode(unittest.TestCase):
    """Tests for _resolve_mode()."""

    def test_explicit_port(self):
        result = cit._resolve_mode("port", port=8080, health="/", baseline=None)
        self.assertEqual(result, "port")

    def test_explicit_shell(self):
        result = cit._resolve_mode("shell", port=None, health=None, baseline=None)
        self.assertEqual(result, "shell")

    def test_auto_detect_port(self):
        result = cit._resolve_mode("", port=8080, health=None, baseline=None)
        self.assertEqual(result, "port")

    def test_auto_detect_health(self):
        result = cit._resolve_mode("", port=8080, health="/api", baseline=None)
        self.assertEqual(result, "health")

    def test_auto_detect_shell(self):
        result = cit._resolve_mode("", port=None, health=None, baseline=None)
        self.assertEqual(result, "shell")

    @patch("dbuild.test._check_screenshot_deps", return_value=[])
    def test_auto_detect_screenshot_with_deps(self, _mock):
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            baseline = Path(f.name)
            result = cit._resolve_mode("", port=8080, health="/", baseline=baseline)
            self.assertEqual(result, "screenshot")

    @patch("dbuild.test._check_screenshot_deps", return_value=["py311-selenium (python package)"])
    def test_auto_detect_screenshot_downgrades_without_deps(self, _mock):
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            baseline = Path(f.name)
            result = cit._resolve_mode("", port=8080, health="/", baseline=baseline)
            self.assertEqual(result, "health")

    @patch("dbuild.test._check_screenshot_deps", return_value=["chromium (/usr/local/bin/chrome)"])
    def test_explicit_screenshot_downgrades_without_deps(self, _mock):
        result = cit._resolve_mode("screenshot", port=8080, health="/", baseline=None)
        self.assertEqual(result, "health")

    @patch("dbuild.test._check_screenshot_deps", return_value=["everything missing"])
    def test_screenshot_downgrades_to_shell_without_port(self, _mock):
        result = cit._resolve_mode("screenshot", port=None, health=None, baseline=None)
        self.assertEqual(result, "shell")


class TestCheckScreenshotDeps(unittest.TestCase):
    """Tests for _check_screenshot_deps()."""

    def test_returns_list(self):
        result = cit._check_screenshot_deps()
        self.assertIsInstance(result, list)
        # On this host, at least skimage is missing
        # Just verify it returns strings
        for item in result:
            self.assertIsInstance(item, str)


class TestTestPort(unittest.TestCase):
    """Tests for _test_port() with a real socket."""

    def test_port_listening(self):
        # Start a listener on a random port
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            result = cit._test_port("127.0.0.1", port, timeout=5)
            self.assertTrue(result)
        finally:
            srv.close()

    def test_port_not_listening(self):
        # Use a port that's almost certainly not listening
        result = cit._test_port("127.0.0.1", 19, timeout=2)
        self.assertFalse(result)


class TestTestHealth(unittest.TestCase):
    """Tests for _test_health() with a real HTTP server."""

    @classmethod
    def setUpClass(cls):
        """Start a simple HTTP server on a random port."""
        cls.handler = http.server.SimpleHTTPRequestHandler
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), cls.handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_health_ok(self):
        result = cit._test_health("127.0.0.1", self.port, "/", timeout=5)
        self.assertTrue(result)

    def test_health_404_still_ok(self):
        # 404 is not 502/503, so it counts as healthy
        result = cit._test_health("127.0.0.1", self.port, "/nonexistent", timeout=5)
        self.assertTrue(result)

    def test_health_connection_refused(self):
        result = cit._test_health("127.0.0.1", 19, "/", timeout=2)
        self.assertFalse(result)


class TestWriteJsonResult(unittest.TestCase):
    """Tests for _write_json_result()."""

    def test_writes_valid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name
        try:
            results = {
                "shell": "pass",
                "port": "pass",
                "health": "skip",
                "screenshot": "skip",
                "verify": "skip",
            }
            cit._write_json_result(path, "myimage:latest", "port", results, True)

            with open(path) as f:
                data = json.load(f)
            self.assertEqual(data["image"], "myimage:latest")
            self.assertEqual(data["mode"], "port")
            self.assertEqual(data["result"], "pass")
            self.assertEqual(data["shell"], "pass")
            self.assertEqual(data["port"], "pass")
            self.assertEqual(data["health"], "skip")
            self.assertIn("timestamp", data)
        finally:
            os.unlink(path)

    def test_fail_result(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name
        try:
            results = {"shell": "pass", "port": "fail", "health": "skip",
                        "screenshot": "skip", "verify": "skip"}
            cit._write_json_result(path, "myimage:latest", "port", results, False)
            with open(path) as f:
                data = json.load(f)
            self.assertEqual(data["result"], "fail")
        finally:
            os.unlink(path)


class TestEmergencyCleanup(unittest.TestCase):
    """Tests for the cleanup registry."""

    def test_cleanup_registry_starts_empty(self):
        # Save and restore
        saved = list(cit._cleanup_targets)
        cit._cleanup_targets.clear()
        self.assertEqual(len(cit._cleanup_targets), 0)
        cit._cleanup_targets.extend(saved)

    @patch("dbuild.test.podman.stop")
    @patch("dbuild.test.podman.rm")
    def test_emergency_cleanup_removes_containers(self, mock_rm, mock_stop):
        cit._cleanup_targets.clear()
        cit._cleanup_targets.append((None, "test-container"))
        cit._emergency_cleanup()
        mock_stop.assert_called_once_with("test-container")
        mock_rm.assert_called_once_with("test-container")
        self.assertEqual(len(cit._cleanup_targets), 0)

    @patch("dbuild.test.podman.compose_down")
    def test_emergency_cleanup_removes_compose(self, mock_down):
        cit._cleanup_targets.clear()
        cit._cleanup_targets.append(("/path/to/compose.yaml", None))
        cit._emergency_cleanup()
        mock_down.assert_called_once_with("/path/to/compose.yaml")
        self.assertEqual(len(cit._cleanup_targets), 0)

    @patch("dbuild.test.podman.stop", side_effect=Exception("boom"))
    @patch("dbuild.test.podman.rm")
    def test_emergency_cleanup_swallows_errors(self, mock_rm, mock_stop):
        cit._cleanup_targets.clear()
        cit._cleanup_targets.append((None, "test-container"))
        # Should not raise
        cit._emergency_cleanup()
        self.assertEqual(len(cit._cleanup_targets), 0)


class TestCopyFile(unittest.TestCase):
    """Tests for _copy_file()."""

    def test_copies_content(self):
        with tempfile.NamedTemporaryFile(delete=False) as src:
            src.write(b"hello world")
            src_path = src.name
        with tempfile.NamedTemporaryFile(delete=False) as dst:
            dst_path = dst.name
        try:
            cit._copy_file(src_path, dst_path)
            with open(dst_path, "rb") as f:
                self.assertEqual(f.read(), b"hello world")
        finally:
            os.unlink(src_path)
            os.unlink(dst_path)


if __name__ == "__main__":
    unittest.main()
