"""Shared download / MIME helpers for the screenshot and logo commands."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import urllib.request

from dbuild import log


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


def download_to_temp(url: str) -> str:
    """Download *url* to a temp file and return its path.

    Raises on failure (after cleaning up the temp file). On success the caller
    owns the returned path and is responsible for unlinking it.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tmp") as tf:
        tmp_path = tf.name
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "dbuild/1.0"})
        with urllib.request.urlopen(req) as resp, open(tmp_path, "wb") as f:
            shutil.copyfileobj(resp, f)
    except Exception:
        os.unlink(tmp_path)
        raise
    return tmp_path
