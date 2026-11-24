"""Tests for dbuild.fetch shared download/MIME helpers and upstream_assets."""

from __future__ import annotations

import http.server
import os
import stat
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from dbuild import fetch, upstream_assets


class _MockServer:
    """Minimal localhost HTTP server for download_to_temp redirect and cap tests."""

    def __init__(self, handler_cls):
        self._srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._srv.shutdown()
        self._srv.server_close()


class TestGithubRawUrl(unittest.TestCase):
    def test_blob_url_converted(self):
        url = "https://github.com/owner/repo/blob/main/path/logo.svg"
        self.assertEqual(
            fetch.github_raw_url(url),
            "https://raw.githubusercontent.com/owner/repo/main/path/logo.svg",
        )

    def test_raw_url_unchanged(self):
        url = "https://raw.githubusercontent.com/owner/repo/main/logo.svg"
        self.assertEqual(fetch.github_raw_url(url), url)

    def test_non_github_unchanged(self):
        url = "https://example.com/assets/logo.png"
        self.assertEqual(fetch.github_raw_url(url), url)

    def test_github_non_blob_unchanged(self):
        url = "https://github.com/owner/repo/releases/download/v1/logo.png"
        self.assertEqual(fetch.github_raw_url(url), url)


class TestDownloadToTemp(unittest.TestCase):
    def test_rejects_non_http_schemes(self):
        for bad in ("file:///etc/passwd", "ftp://example.com/img.png", "/etc/passwd", "https://"):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                fetch.download_to_temp(bad)

    def test_downloads_valid_http_response(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"PNG-BYTES"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with _MockServer(Handler) as srv:
            tmp = fetch.download_to_temp(f"http://127.0.0.1:{srv.port}/img.png")
            try:
                self.assertEqual(Path(tmp).read_bytes(), b"PNG-BYTES")
            finally:
                os.unlink(tmp)

    def test_rejects_redirect_to_ftp_and_file_schemes(self):
        for target, exc_types in (
            ("ftp://example.com/evil", ValueError),
            ("file:///etc/passwd", (ValueError, urllib.error.HTTPError)),
        ):
            class Handler(http.server.BaseHTTPRequestHandler):
                location = target

                def do_GET(self):
                    self.send_response(302)
                    self.send_header("Location", self.location)
                    self.end_headers()

                def log_message(self, *args):
                    pass

            with (
                self.subTest(target=target),
                _MockServer(Handler) as srv,
                self.assertRaises(exc_types),
            ):
                fetch.download_to_temp(f"http://127.0.0.1:{srv.port}/evil")

    def test_enforces_max_bytes_limit(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"A" * 2048
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with _MockServer(Handler) as srv, self.assertRaises(ValueError):
            fetch.download_to_temp(f"http://127.0.0.1:{srv.port}/big", max_bytes=1024)


class TestUpstreamAssets(unittest.TestCase):
    def test_stem_from_url_sanitizes_dots(self):
        self.assertEqual(upstream_assets._stem_from_url("https://example.com/.."), "screenshot")
        self.assertEqual(upstream_assets._stem_from_url("https://example.com/."), "screenshot")
        self.assertEqual(upstream_assets._stem_from_url("https://example.com/screen.png"), "screen")

    def test_download_screenshot_rejects_unknown_mime_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / ".daemonless").mkdir()
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(b"<html>not an image</html>")
                tmp_path = tmp.name

            with (
                patch("dbuild.fetch.download_to_temp", return_value=tmp_path),
                patch("dbuild.fetch.detect_mime", return_value="text/html"),
            ):
                rc = upstream_assets.download_screenshot("https://example.com/shot.png", repo)

            self.assertEqual(rc, 1)
            self.assertFalse(Path(tmp_path).exists())
            self.assertFalse((repo / ".daemonless" / "screenshots").exists())

    def test_download_screenshot_sets_0644_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / ".daemonless").mkdir()
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(b"\x89PNG\r\n\x1a\n")
                tmp_path = tmp.name
            os.chmod(tmp_path, 0o600)

            with (
                patch("dbuild.fetch.download_to_temp", return_value=tmp_path),
                patch("dbuild.fetch.detect_mime", return_value="image/png"),
            ):
                rc = upstream_assets.download_screenshot("https://example.com/shot.png", repo)

            saved = repo / ".daemonless" / "screenshots" / "shot.png"
            self.assertEqual(rc, 0)
            self.assertTrue(saved.exists())
            self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o644)

    def test_process_logo_sets_0644_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / ".daemonless").mkdir()
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(b"<svg/>")
                tmp_path = tmp.name
            os.chmod(tmp_path, 0o600)

            with (
                patch("dbuild.fetch.download_to_temp", return_value=tmp_path),
                patch("dbuild.fetch.detect_mime", return_value="image/svg+xml"),
            ):
                rc = upstream_assets.process_logo("https://example.com/logo.svg", repo)

            saved = repo / ".daemonless" / "logo.svg"
            self.assertEqual(rc, 0)
            self.assertTrue(saved.exists())
            self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o644)


if __name__ == "__main__":
    unittest.main()
