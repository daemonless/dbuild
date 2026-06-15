"""Unit tests for dbuild.image parsing utilities."""

from __future__ import annotations

import contextlib
import os
import tempfile
import unittest

from dbuild.image import image_dimensions


class TestImageDimensions(unittest.TestCase):
    """Tests for image_dimensions()."""

    def setUp(self):
        self.temp_files: list[str] = []

    def tearDown(self):
        for path in self.temp_files:
            with contextlib.suppress(OSError):
                os.unlink(path)

    def _write_temp(self, data: bytes | str) -> str:
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            if isinstance(data, str):
                tf.write(data.encode("utf-8"))
            else:
                tf.write(data)
            path = tf.name
        self.temp_files.append(path)
        return path

    def test_png_dimensions(self):
        # Header: \x89PNG\r\n\x1a\n (8 bytes)
        # Dummy IHDR size + type: \x00\x00\x00\rIHDR (8 bytes)
        # Width: 100 (4 bytes big-endian: \x00\x00\x00\x64)
        # Height: 200 (4 bytes big-endian: \x00\x00\x00\xc8)
        png_data = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\x0dIHDR"
            b"\x00\x00\x00\x64"
            b"\x00\x00\x00\xc8"
        )
        path = self._write_temp(png_data)
        dims = image_dimensions(path, ".png")
        self.assertEqual(dims, (100.0, 200.0))

    def test_png_invalid(self):
        path = self._write_temp(b"not a png file indeed")
        self.assertIsNone(image_dimensions(path, ".png"))

    def test_gif_dimensions_89a(self):
        # Header: GIF89a (6 bytes)
        # Width: 150 (2 bytes little-endian: \x96\x00)
        # Height: 300 (2 bytes little-endian: \x2c\x01)
        gif_data = b"GIF89a\x96\x00\x2c\x01"
        path = self._write_temp(gif_data)
        dims = image_dimensions(path, ".gif")
        self.assertEqual(dims, (150.0, 300.0))

    def test_gif_dimensions_87a(self):
        gif_data = b"GIF87a\x64\x00\x64\x00"
        path = self._write_temp(gif_data)
        dims = image_dimensions(path, ".gif")
        self.assertEqual(dims, (100.0, 100.0))

    def test_gif_invalid(self):
        path = self._write_temp(b"GIF88a\x00\x00\x00\x00")
        self.assertIsNone(image_dimensions(path, ".gif"))

    def test_jpeg_dimensions(self):
        # SOI: \xff\xd8
        # APP0: \xff\xe0, len: 16 (\x00\x10), 14 dummy bytes
        # SOF0: \xff\xc0, len: 11 (\x00\x0b), precision: 8, height: 120 (\x00\x78), width: 240 (\x00\xf0)
        jpeg_data = (
            b"\xff\xd8"
            b"\xff\xe0\x00\x10" + b"\x00" * 14 +
            b"\xff\xc0\x00\x0b\x08\x00\x78\x00\xf0"
        )
        path = self._write_temp(jpeg_data)
        dims = image_dimensions(path, ".jpg")
        self.assertEqual(dims, (240.0, 120.0))

    def test_jpeg_marker_skipping(self):
        # Walks past APP0, APP1, then finds SOF2 (\xff\xc2)
        jpeg_data = (
            b"\xff\xd8"
            b"\xff\xe0\x00\x05\x01\x02\x03"
            b"\xff\xe1\x00\x04\x01\x02"
            b"\xff\xc2\x00\x0b\x08\x01\x00\x02\x00"
        )
        path = self._write_temp(jpeg_data)
        dims = image_dimensions(path, ".jpeg")
        self.assertEqual(dims, (512.0, 256.0))

    def test_jpeg_invalid(self):
        # Invalid start
        path = self._write_temp(b"\xff\xd9\x00\x00")
        self.assertIsNone(image_dimensions(path, ".jpg"))

    def test_webp_vp8x(self):
        # RIFF header: RIFF (4), size (4), WEBP (4)
        # VP8X chunk: VP8X (4), size: 10 (\x0a\x00\x00\x00)
        # Flags (4), Width-1: 399 (\x8e\x01\x00), Height-1: 499 (\xf3\x01\x00)
        webp_data = (
            b"RIFF\x22\x00\x00\x00WEBP"
            b"VP8X\x0a\x00\x00\x00"
            b"\x00\x00\x00\x00\x8f\x01\x00\xf3\x01\x00"
        )
        path = self._write_temp(webp_data)
        dims = image_dimensions(path, ".webp")
        self.assertEqual(dims, (400.0, 500.0))

    def test_webp_vp8l(self):
        # RIFF header
        # VP8L chunk: VP8L (4), size: 5 (\x05\x00\x00\x00)
        # VP8L signature: \x2f
        # 4 bytes packing: width (14 bits), height (14 bits)
        # Width: 120 (119 = 0b00000001110111 = 0x0077)
        # Height: 240 (239 = 0b00000011101111 = 0x00ef)
        # Pack value: (height << 14) | width -> (0x00ef << 14) | 0x0077 = 0x3bc077 -> \x77\xc0\x3b\x00
        webp_data = (
            b"RIFF\x1d\x00\x00\x00WEBP"
            b"VP8L\x05\x00\x00\x00"
            b"\x2f\x77\xc0\x3b\x00"
        )
        path = self._write_temp(webp_data)
        dims = image_dimensions(path, ".webp")
        self.assertEqual(dims, (120.0, 240.0))

    def test_webp_vp8_lossy(self):
        # RIFF header
        # VP8 chunk: VP8 (4), size: 20 (\x14\x00\x00\x00)
        # 10 bytes frame tag
        # Sync code: \x9d\x01\x2a
        # Width: 300 (\x2c\x01), Height: 150 (\x96\x00)
        webp_data = (
            b"RIFF\x20\x00\x00\x00WEBP"
            b"VP8 \x14\x00\x00\x00"
            + b"\x00" * 10 +
            b"\x9d\x01\x2a"
            b"\x2c\x01\x96\x00"
        )
        path = self._write_temp(webp_data)
        dims = image_dimensions(path, ".webp")
        self.assertEqual(dims, (300.0, 150.0))

    def test_webp_invalid(self):
        path = self._write_temp(b"RIFF\x00\x00\x00\x00WEBD")
        self.assertIsNone(image_dimensions(path, ".webp"))

    def test_svg_viewbox(self):
        svg_content = '<svg viewBox="0 0 80.5 90.1"><path d="..."/></svg>'
        path = self._write_temp(svg_content)
        dims = image_dimensions(path, ".svg")
        self.assertEqual(dims, (80.5, 90.1))

    def test_svg_viewbox_single_quotes_and_spaces(self):
        svg_content = "<svg  viewBox = ' 0   0   120   300 ' >"
        path = self._write_temp(svg_content)
        dims = image_dimensions(path, ".svg")
        self.assertEqual(dims, (120.0, 300.0))

    def test_svg_width_height(self):
        svg_content = '<svg width="200" height="400" viewBox="none">'
        path = self._write_temp(svg_content)
        dims = image_dimensions(path, ".svg")
        self.assertEqual(dims, (200.0, 400.0))

    def test_svg_width_height_px(self):
        svg_content = '<svg width="150px" height="250px">'
        path = self._write_temp(svg_content)
        dims = image_dimensions(path, ".svg")
        self.assertEqual(dims, (150.0, 250.0))

    def test_svg_prolog_too_long(self):
        # A very long prologue exceeding 4096 bytes
        prolog = "<!-- " + "A" * 4096 + " -->"
        svg_content = prolog + '<svg viewBox="0 0 100 100">'
        path = self._write_temp(svg_content)
        dims = image_dimensions(path, ".svg")
        self.assertIsNone(dims)

    def test_svg_invalid(self):
        path = self._write_temp("not an svg tag at all")
        self.assertIsNone(image_dimensions(path, ".svg"))


class TestUpstreamAssetWarnings(unittest.TestCase):
    """Tests for logo_warnings() and screenshot_warnings() in upstream_assets."""

    def setUp(self):
        self.temp_files: list[str] = []

    def tearDown(self):
        for path in self.temp_files:
            with contextlib.suppress(OSError):
                os.unlink(path)

    def _write_temp(self, data: bytes | str) -> str:
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            if isinstance(data, str):
                tf.write(data.encode("utf-8"))
            else:
                tf.write(data)
            path = tf.name
        self.temp_files.append(path)
        return path

    def test_logo_warnings_valid(self):
        # 100x100 png, 24 bytes (well under 150 KB)
        png_data = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\x0dIHDR"
            b"\x00\x00\x00\x64"
            b"\x00\x00\x00\x64"
        )
        path = self._write_temp(png_data)
        from dbuild.upstream_assets import logo_warnings
        warnings = logo_warnings(path, ".png")
        self.assertEqual(warnings, [])

    def test_logo_warnings_oversized(self):
        # 100x100 png but padded to > 150 KB
        png_data = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\x0dIHDR"
            b"\x00\x00\x00\x64"
            b"\x00\x00\x00\x64"
            + b"\x00" * (151 * 1024)
        )
        path = self._write_temp(png_data)
        from dbuild.upstream_assets import logo_warnings
        warnings = logo_warnings(path, ".png")
        self.assertTrue(any(">150 KB" in w for w in warnings))

    def test_logo_warnings_non_square(self):
        # 100x200 png, 24 bytes
        png_data = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\x0dIHDR"
            b"\x00\x00\x00\x64"
            b"\x00\x00\x00\xc8"
        )
        path = self._write_temp(png_data)
        from dbuild.upstream_assets import logo_warnings
        warnings = logo_warnings(path, ".png")
        self.assertTrue(any("non-square" in w for w in warnings))

    def test_logo_warnings_close_to_square(self):
        # 100x120 png (ratio 0.833, within new 0.8 - 1.25 range)
        png_data = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\x0dIHDR"
            b"\x00\x00\x00\x64"
            b"\x00\x00\x00\x78"
        )
        path = self._write_temp(png_data)
        from dbuild.upstream_assets import logo_warnings
        warnings = logo_warnings(path, ".png")
        self.assertEqual(warnings, [])


    def test_screenshot_warnings_valid(self):
        # 800x600 jpeg, 24 bytes
        jpeg_data = (
            b"\xff\xd8"
            b"\xff\xe0\x00\x10" + b"\x00" * 14 +
            b"\xff\xc0\x00\x0b\x08\x02\x58\x03\x20"
        )
        path = self._write_temp(jpeg_data)
        from dbuild.upstream_assets import screenshot_warnings
        warnings = screenshot_warnings(path, ".jpg")
        self.assertEqual(warnings, [])

    def test_screenshot_warnings_too_tall(self):
        # 100x300 jpeg (ratio 0.33 < 0.5)
        jpeg_data = (
            b"\xff\xd8"
            b"\xff\xe0\x00\x10" + b"\x00" * 14 +
            b"\xff\xc0\x00\x0b\x08\x01\x2c\x00\x64"
        )
        path = self._write_temp(jpeg_data)
        from dbuild.upstream_assets import screenshot_warnings
        warnings = screenshot_warnings(path, ".jpg")
        self.assertTrue(any("very tall" in w for w in warnings))

    def test_screenshot_warnings_oversized(self):
        # 800x600 jpeg but padded to > 1 MB
        jpeg_data = (
            b"\xff\xd8"
            b"\xff\xe0\x00\x10" + b"\x00" * 14 +
            b"\xff\xc0\x00\x0b\x08\x02\x58\x03\x20"
            + b"\x00" * (1025 * 1024)
        )
        path = self._write_temp(jpeg_data)
        from dbuild.upstream_assets import screenshot_warnings
        warnings = screenshot_warnings(path, ".jpg")
        self.assertTrue(any(">1 MB" in w for w in warnings))

