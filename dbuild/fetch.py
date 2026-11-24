"""Shared download / MIME helpers for the screenshot and logo commands."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request

from dbuild import log

_ALLOWED_SCHEMES = frozenset({"http", "https"})
DEFAULT_TIMEOUT = 30.0
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


def _validate_http_url(url: str) -> None:
    """Reject non-HTTP(S) URLs and URLs without a network host."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.netloc:
        raise ValueError(f"Unsupported URL (expected http or https): {url}")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Ensure HTTP redirects never jump to local or non-HTTP(S) schemes."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def detect_mime(path: str) -> str:
    """Return the MIME type of *path* using ``file --mime-type``."""
    if not shutil.which("file"):
        log.warn("'file' command not found — cannot detect MIME type")
        return ""
    result = subprocess.run(
        ["file", "--mime-type", "-b", path],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def github_raw_url(url: str) -> str:
    """Convert a GitHub blob viewer URL to the raw content URL.

    https://github.com/owner/repo/blob/branch/path/file.png
    → https://raw.githubusercontent.com/owner/repo/branch/path/file.png
    """
    if "github.com" in url and "/blob/" in url:
        return url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
    return url


def download_to_temp(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> str:
    """Download *url* to a temp file and return its path.

    Only ``http`` and ``https`` URLs (including redirects) are allowed, bounded
    by *timeout* seconds and *max_bytes* bytes.
    Raises on failure (after cleaning up the temp file). On success the caller
    owns the returned path and is responsible for unlinking it.
    """
    _validate_http_url(url)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tmp") as tf:
        tmp_path = tf.name
    try:
        opener = urllib.request.build_opener(_SafeRedirectHandler())
        req = urllib.request.Request(url, headers={"User-Agent": "dbuild/1.0"})
        with opener.open(req, timeout=timeout) as resp, open(tmp_path, "wb") as f:
            total = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(
                        f"Download exceeds {max_bytes // (1024 * 1024)} MB limit: {url}"
                    )
                f.write(chunk)
    except Exception:
        os.unlink(tmp_path)
        raise
    return tmp_path
