"""Pure-Python image dimension extraction utilities.

Enables extracting dimensions of PNG, JPEG, GIF, WebP, and SVG images
without using external dependencies like Pillow (PIL).
"""

from __future__ import annotations

import re


def _png_dimensions(path: str) -> tuple[float, float] | None:
    """Return (width, height) of a PNG file or None."""
    try:
        with open(path, "rb") as f:
            data = f.read(24)
            if len(data) >= 24 and data[0:8] == b"\x89PNG\r\n\x1a\n":
                import struct
                w, h = struct.unpack(">II", data[16:24])
                return float(w), float(h)
    except Exception:
        pass
    return None


def _gif_dimensions(path: str) -> tuple[float, float] | None:
    """Return (width, height) of a GIF file or None."""
    try:
        with open(path, "rb") as f:
            data = f.read(10)
            if len(data) >= 10 and data[0:6] in (b"GIF87a", b"GIF89a"):
                import struct
                w, h = struct.unpack("<HH", data[6:10])
                return float(w), float(h)
    except Exception:
        pass
    return None


def _jpeg_dimensions(path: str) -> tuple[float, float] | None:
    """Return (width, height) of a JPEG file or None by parsing markers."""
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"\xff\xd8":
                return None
            while True:
                marker = f.read(2)
                if not marker or marker[0] != 0xff:
                    break
                marker_type = marker[1]
                while marker_type == 0xff:
                    next_byte = f.read(1)
                    if not next_byte:
                        return None
                    marker_type = next_byte[0]
                if marker_type in (0xda, 0xd9):
                    break
                len_bytes = f.read(2)
                if len(len_bytes) < 2:
                    break
                chunk_len = int.from_bytes(len_bytes, "big")
                if 0xc0 <= marker_type <= 0xcf and marker_type not in (0xc4, 0xc8, 0xcc):
                    data = f.read(chunk_len - 2)
                    if len(data) >= 5:
                        height = int.from_bytes(data[1:3], "big")
                        width = int.from_bytes(data[3:5], "big")
                        return float(width), float(height)
                    break
                else:
                    f.seek(chunk_len - 2, 1)
    except Exception:
        pass
    return None


def _webp_dimensions(path: str) -> tuple[float, float] | None:
    """Return (width, height) of a WebP file or None by parsing RIFF chunks."""
    try:
        with open(path, "rb") as f:
            header = f.read(12)
            if len(header) < 12 or header[0:4] != b"RIFF" or header[8:12] != b"WEBP":
                return None
            chunk_hdr = f.read(8)
            if len(chunk_hdr) < 8:
                return None
            chunk_type = chunk_hdr[0:4]
            if chunk_type == b"VP8X":
                data = f.read(10)
                if len(data) >= 10:
                    width = int.from_bytes(data[4:7], "little") + 1
                    height = int.from_bytes(data[7:10], "little") + 1
                    return float(width), float(height)
            elif chunk_type == b"VP8L":
                data = f.read(5)
                if len(data) >= 5 and data[0] == 0x2f:
                    val = int.from_bytes(data[1:5], "little")
                    width = (val & 0x3fff) + 1
                    height = ((val >> 14) & 0x3fff) + 1
                    return float(width), float(height)
            elif chunk_type == b"VP8 ":
                f.seek(10, 1)
                sync = f.read(3)
                if sync == b"\x9d\x01\x2a":
                    w_bytes = f.read(2)
                    h_bytes = f.read(2)
                    if len(w_bytes) == 2 and len(h_bytes) == 2:
                        width = int.from_bytes(w_bytes, "little") & 0x3fff
                        height = int.from_bytes(h_bytes, "little") & 0x3fff
                        return float(width), float(height)
    except Exception:
        pass
    return None


def _svg_dimensions(path: str) -> tuple[float, float] | None:
    """Attempt to parse viewBox or width/height from SVG file."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            content = f.read(4096)  # Read beginning of SVG
        # Look for <svg ...>
        svg_tag = re.search(r"<svg([^>]+)>", content, re.IGNORECASE)
        if not svg_tag:
            return None
        attrs = svg_tag.group(1)

        # Try viewBox first: viewBox="x y width height"
        viewbox_match = re.search(
            r'viewBox\s*=\s*["\']\s*([0-9.-]+)\s+([0-9.-]+)\s+([0-9.-]+)\s+([0-9.-]+)\s*["\']',
            attrs,
            re.IGNORECASE
        )
        if viewbox_match:
            w = float(viewbox_match.group(3))
            h = float(viewbox_match.group(4))
            if w > 0 and h > 0:
                return w, h

        # Try width and height attributes
        w_match = re.search(r'width\s*=\s*["\']\s*([0-9.-]+)\s*(?:px)?\s*["\']', attrs, re.IGNORECASE)
        h_match = re.search(r'height\s*=\s*["\']\s*([0-9.-]+)\s*(?:px)?\s*["\']', attrs, re.IGNORECASE)
        if w_match and h_match:
            return float(w_match.group(1)), float(h_match.group(1))
    except Exception:
        pass
    return None


def image_dimensions(path: str, ext: str) -> tuple[float, float] | None:
    """Return (width, height) of an image file or None."""
    ext = ext.lower()
    if ext == ".svg":
        return _svg_dimensions(path)
    elif ext == ".png":
        return _png_dimensions(path)
    elif ext == ".gif":
        return _gif_dimensions(path)
    elif ext in (".jpg", ".jpeg"):
        return _jpeg_dimensions(path)
    elif ext == ".webp":
        return _webp_dimensions(path)
    return None
