"""Unit tests for dbuild.verify."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

np = None
try:
    import numpy as np
    from skimage import io

    from dbuild.verify import verify
except ImportError:
    pass


def _write_png(d: str, name: str, img) -> str:
    path = str(Path(d) / name)
    io.imsave(path, img)
    return path


def _blank_img():
    return np.full((64, 64, 3), 128, dtype=np.uint8)


def _ui_img():
    """High-contrast checkerboard of 8px blocks -- plenty of Sobel edges.

    (1px tiles won't do: the Sobel kernel cancels on alternating pixels.)
    """
    tile = np.array([[0, 255], [255, 0]], dtype=np.uint8)
    board = np.kron(np.tile(tile, (4, 4)), np.ones((8, 8), dtype=np.uint8))
    return np.stack([board] * 3, axis=-1)


@unittest.skipIf(np is None, "scikit-image/numpy not installed")
class TestVerify(unittest.TestCase):
    def test_blank_image_fails_with_scores(self):
        with tempfile.TemporaryDirectory() as d:
            passed, msg, metrics = verify(_write_png(d, "shot.png", _blank_img()))
        self.assertFalse(passed)
        self.assertIn("blank", msg)
        self.assertIn("threshold", msg)
        self.assertIn("blank_std", metrics)
        self.assertIn("edge_ratio", metrics)

    def test_edge_failure_reports_ratio_and_hint(self):
        """A noisy-but-flat image passes blank but fails the edge gate."""
        rng = np.random.default_rng(42)
        img = rng.integers(100, 160, size=(64, 64, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as d:
            passed, msg, metrics = verify(_write_png(d, "shot.png", img))
        self.assertFalse(passed)
        self.assertIn("edge ratio", msg)
        self.assertIn("edge_threshold", msg)  # tuning hint
        self.assertGreaterEqual(metrics["blank_std"], 0)

    def test_edge_threshold_override(self):
        """The same sparse image passes with a per-image edge_threshold."""
        rng = np.random.default_rng(42)
        img = rng.integers(100, 160, size=(64, 64, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as d:
            passed, _msg, _metrics = verify(
                _write_png(d, "shot.png", img), edge_threshold=0.0
            )
        self.assertTrue(passed)

    def test_identical_baseline_passes_and_records_ssim(self):
        with tempfile.TemporaryDirectory() as d:
            shot = _write_png(d, "shot.png", _ui_img())
            base = _write_png(d, "baseline.png", _ui_img())
            passed, msg, metrics = verify(shot, base)
        self.assertTrue(passed)
        self.assertIn("SSIM", msg)
        self.assertAlmostEqual(metrics["ssim"], 1.0, places=3)

    def test_mismatched_baseline_fails_with_score(self):
        with tempfile.TemporaryDirectory() as d:
            shot = _write_png(d, "shot.png", _ui_img())
            stripes = np.kron(
                np.tile(np.array([[0], [255]], dtype=np.uint8), (4, 8)),
                np.ones((8, 8), dtype=np.uint8),
            )
            base = _write_png(d, "baseline.png", np.stack([stripes] * 3, axis=-1))
            passed, msg, metrics = verify(shot, base, threshold=0.99)
        self.assertFalse(passed)
        self.assertIn("below threshold", msg)
        self.assertIn("ssim", metrics)
        self.assertLess(metrics["ssim"], 0.99)

    def test_unreadable_image(self):
        passed, msg, metrics = verify("/nonexistent/shot.png")
        self.assertFalse(passed)
        self.assertIn("Cannot read image", msg)
        self.assertEqual(metrics, {})


if __name__ == "__main__":
    unittest.main()
