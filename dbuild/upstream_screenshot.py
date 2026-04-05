"""Download and deduplicate upstream screenshots into .daemonless/screenshots/.

Usage::

    dbuild screenshot https://example.com/path/to/image.png

The file is downloaded to a temp location, its MIME type is detected with
``file --mime-type`` (so the extension reflects reality even if the URL lies),
and a SHA-256 digest is computed.  If an identical file already exists in
``.daemonless/screenshots/`` it is skipped.  Otherwise it is saved as
``<basename>.<ext>`` where *basename* comes from the URL and *ext* is derived
from the detected MIME type.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from dbuild import log

# MIME type → canonical extension
_MIME_EXT: dict[str, str] = {
    "image/png":      ".png",
    "image/jpeg":     ".jpg",
    "image/gif":      ".gif",
    "image/webp":     ".webp",
    "image/avif":     ".avif",
    "image/svg+xml":  ".svg",
    "image/bmp":      ".bmp",
    "image/tiff":     ".tiff",
    "video/mp4":      ".mp4",
    "video/webm":     ".webm",
    "video/ogg":      ".ogv",
    "video/quicktime": ".mov",
}


def _detect_mime(path: str) -> str:
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


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _existing_hashes(screenshots_dir: Path) -> dict[str, Path]:
    """Return {sha256_hex: path} for all files in *screenshots_dir*."""
    result: dict[str, Path] = {}
    if not screenshots_dir.exists():
        return result
    for p in screenshots_dir.iterdir():
        if p.is_file():
            result[_sha256(str(p))] = p
    return result


def _stem_from_url(url: str) -> str:
    """Derive a safe filename stem from a URL (no extension, no query string)."""
    parsed = urlparse(url)
    basename = os.path.basename(parsed.path)
    stem = Path(basename).stem if basename else ""
    # Strip GitHub blob path prefix artefacts like "raw" or "blob"
    if not stem or stem in ("raw", "blob", "main", "master"):
        stem = ""
    # Sanitize: keep alphanumerics, hyphens, underscores, dots
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in stem)
    return safe or "screenshot"


def _github_raw_url(url: str) -> str:
    """Convert a GitHub blob viewer URL to the raw content URL."""
    # https://github.com/owner/repo/blob/branch/path/file.png
    # → https://raw.githubusercontent.com/owner/repo/branch/path/file.png
    if "github.com" in url and "/blob/" in url:
        return url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
    return url


def download_screenshot(url: str, repo_dir: Path | None = None) -> int:
    """Download *url* and save to ``.daemonless/screenshots/`` if new.

    Returns 0 on success (including skip), 1 on error.
    """
    base = repo_dir or Path.cwd()
    screenshots_dir = base / ".daemonless" / "screenshots"

    raw_url = _github_raw_url(url)
    log.info(f"Downloading: {raw_url}")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".tmp") as tf:
        tmp_path = tf.name

    try:
        req = urllib.request.Request(raw_url, headers={"User-Agent": "dbuild/1.0"})
        with urllib.request.urlopen(req) as resp, open(tmp_path, "wb") as f:
            shutil.copyfileobj(resp, f)
    except Exception as exc:
        log.error(f"Download failed: {exc}")
        os.unlink(tmp_path)
        return 1

    # Detect real MIME type
    mime = _detect_mime(tmp_path)
    ext = _MIME_EXT.get(mime)
    if not ext:
        log.warn(f"Unrecognised MIME type '{mime}' — defaulting to .png")
        ext = ".png"
    log.info(f"Detected type: {mime} → {ext}")

    # Hash and check for duplicates
    digest = _sha256(tmp_path)
    log.info(f"SHA-256: {digest[:16]}...")
    existing = _existing_hashes(screenshots_dir)
    if digest in existing:
        log.info(f"Already exists as {existing[digest].name} — skipping")
        os.unlink(tmp_path)
        return 0

    # Choose destination filename
    stem = _stem_from_url(url)
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    dest = screenshots_dir / f"{stem}{ext}"
    # If the stem-based name is taken (different content), append a counter
    counter = 2
    while dest.exists():
        dest = screenshots_dir / f"{stem}_{counter}{ext}"
        counter += 1

    shutil.move(tmp_path, str(dest))
    log.success(f"Saved: {dest.relative_to(base)}")
    return 0


def run(args: argparse.Namespace) -> int:
    base = Path.cwd()
    if not (base / "compose.yaml").exists() or not (base / ".daemonless").is_dir():
        log.error("Not a dbuild image repo (missing compose.yaml or .daemonless/)")
        return 1

    urls: list[str] = args.urls
    rc = 0
    for url in urls:
        result = download_screenshot(url, base)
        if result != 0:
            rc = result
    return rc
