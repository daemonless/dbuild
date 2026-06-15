"""Download and manage upstream assets (screenshots and logos) in the repository.

Provides commands to fetch screenshots into .daemonless/screenshots/ and logos
into .daemonless/logo.<ext>.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

from dbuild import fetch, log
from dbuild.image import image_dimensions

# --- Screenshots Configuration ---
_SCREENSHOT_MIME_EXT: dict[str, str] = {
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
_SCREENSHOT_MIN_ASPECT_RATIO = 0.5


# --- Logos Configuration ---
_LOGO_MIME_EXT: dict[str, str] = {
    "image/svg+xml":  ".svg",
    "image/png":      ".png",
}
_LOGO_MIN_ASPECT_RATIO = 0.95
_LOGO_MAX_ASPECT_RATIO = 1.05


# --- Shared Helpers ---
def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# --- Screenshot Logic ---
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
    if not stem or stem in ("raw", "blob", "main", "master"):
        stem = ""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in stem)
    return safe or "screenshot"


def download_screenshot(url: str, repo_dir: Path | None = None) -> int:
    """Download *url* and save to ``.daemonless/screenshots/`` if new.

    Returns 0 on success (including skip), 1 on error.
    """
    base = repo_dir or Path.cwd()
    screenshots_dir = base / ".daemonless" / "screenshots"

    raw_url = fetch.github_raw_url(url)
    log.info(f"Downloading: {raw_url}")
    try:
        tmp_path = fetch.download_to_temp(raw_url)
    except Exception as exc:
        log.error(f"Download failed: {exc}")
        return 1

    mime = fetch.detect_mime(tmp_path)
    ext = _SCREENSHOT_MIME_EXT.get(mime)
    if not ext:
        log.warn(f"Unrecognised MIME type '{mime}' — defaulting to .png")
        ext = ".png"
    log.info(f"Detected type: {mime} → {ext}")

    if mime.startswith("image/"):
        dims = image_dimensions(tmp_path, ext)
        if dims:
            w, h = dims
            if h > 0 and w / h < _SCREENSHOT_MIN_ASPECT_RATIO:
                log.warn(
                    f"Image is very tall: {w}x{h} (w/h {w / h:.2f}). "
                    "It will look bad next to landscape screenshots in the "
                    "gallery — prefer a wider-than-tall capture if you can."
                )

    digest = _sha256(tmp_path)
    log.info(f"SHA-256: {digest[:16]}...")
    existing = _existing_hashes(screenshots_dir)
    if digest in existing:
        log.info(f"Already exists as {existing[digest].name} — skipping")
        os.unlink(tmp_path)
        return 0

    stem = _stem_from_url(url)
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    dest = screenshots_dir / f"{stem}{ext}"
    counter = 2
    while dest.exists():
        dest = screenshots_dir / f"{stem}_{counter}{ext}"
        counter += 1

    shutil.move(tmp_path, str(dest))
    log.success(f"Saved: {dest.relative_to(base)}")
    return 0


# --- Logo Logic ---
def process_logo(source: str, repo_dir: Path | None = None, dark: bool = False) -> int:
    """Download or copy *source* and save to ``.daemonless/logo[-dark].<ext>`` if valid.

    Returns 0 on success, 1 on error.
    """
    base = repo_dir or Path.cwd()
    logo_dir = base / ".daemonless"
    logo_prefix = "logo-dark" if dark else "logo"

    is_url = source.startswith(("http://", "https://"))
    local_file = Path(source) if not is_url else None

    if local_file:
        resolved_file = local_file if local_file.is_absolute() else (base / local_file)
        if not resolved_file.is_file():
            log.error(f"Source is not a valid URL or existing local file: {source}")
            return 1

        log.info(f"Using local logo file: {resolved_file}")
        src_path = str(resolved_file)
    elif is_url:
        raw_url = fetch.github_raw_url(source)
        log.info(f"Downloading logo from: {raw_url}")

        try:
            tmp_path = fetch.download_to_temp(raw_url)
            src_path = tmp_path
        except Exception as exc:
            log.error(f"Download failed: {exc}")
            return 1
    else:
        log.error(f"Source must be a URL or a valid local file path: {source}")
        return 1

    mime = fetch.detect_mime(src_path)
    ext = _LOGO_MIME_EXT.get(mime)
    if not ext:
        log.error(f"Unsupported logo format '{mime}' — only SVG and PNG are supported.")
        if is_url:
            os.unlink(src_path)
        return 1
    log.info(f"Detected type: {mime} → {ext}")

    try:
        file_size = os.path.getsize(src_path)
        if file_size > 150 * 1024:
            log.warn(
                f"Logo file is quite large: {file_size / 1024:.1f} KB. "
                "Logo files should ideally be under 150 KB to avoid repository bloat. "
                "Consider optimizing/compressing it."
            )
    except Exception:
        pass

    dims = image_dimensions(src_path, ext)
    w, h = 0.0, 0.0
    if dims:
        w, h = dims

    if w > 0 and h > 0:
        ratio = w / h
        if ratio < _LOGO_MIN_ASPECT_RATIO or ratio > _LOGO_MAX_ASPECT_RATIO:
            log.warn(
                f"Logo aspect ratio is non-square: {w:.1f}x{h:.1f} (w/h {ratio:.2f}). "
                "Logos should ideally be square (1:1)."
            )
        else:
            log.info(f"Logo dimensions validated: {w:.1f}x{h:.1f} (square ratio)")
    else:
        log.info("Could not extract logo dimensions (skipping aspect ratio validation)")

    logo_dir.mkdir(parents=True, exist_ok=True)

    for old_ext in _LOGO_MIME_EXT.values():
        old_file = logo_dir / f"{logo_prefix}{old_ext}"
        if old_file.exists():
            if os.path.exists(src_path) and os.path.samefile(old_file, src_path):
                continue
            try:
                old_file.unlink()
            except Exception as e:
                log.warn(f"Failed to remove old logo file {old_file}: {e}")

    dest = logo_dir / f"{logo_prefix}{ext}"
    if os.path.exists(src_path) and dest.exists() and os.path.samefile(src_path, dest):
        log.success(f"Logo is already in place: {dest.relative_to(base)}")
        return 0

    try:
        shutil.copy2(src_path, str(dest))
        log.success(f"Saved: {dest.relative_to(base)}")
    except Exception as e:
        log.error(f"Failed to copy logo file to destination: {e}")
        if is_url:
            os.unlink(src_path)
        return 1

    if is_url:
        os.unlink(src_path)

    return 0


# --- Command-line Entry Points ---
def run_screenshot(args: argparse.Namespace) -> int:
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


def run_logo(args: argparse.Namespace) -> int:
    base = Path.cwd()
    if not (base / "compose.yaml").exists() or not (base / ".daemonless").is_dir():
        log.error("Not a dbuild image repo (missing compose.yaml or .daemonless/)")
        return 1

    source: str = args.logo_source
    dark: bool = getattr(args, "dark", False)
    return process_logo(source, base, dark)
