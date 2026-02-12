"""Screenshot verification using scikit-image.

Requires: scikit-image, numpy.  These are optional dependencies --
import errors are caught by the caller (test.py) so the rest of dbuild
works without them.
"""

from __future__ import annotations

import os

import numpy as np
from skimage import color, filters, io, transform
from skimage.metrics import structural_similarity as ssim

# Thresholds (configurable via env)
BLANK_THRESHOLD = float(os.environ.get("VERIFY_BLANK_THRESHOLD", "3"))
EDGE_THRESHOLD = float(os.environ.get("VERIFY_EDGE_THRESHOLD", "0.005"))
SSIM_THRESHOLD = float(os.environ.get("VERIFY_SSIM_THRESHOLD", "0.95"))


def is_blank(img: np.ndarray) -> bool:
    """Return True if the image is mostly one color (blank/failed render)."""
    gray = color.rgb2gray(img) if img.ndim == 3 else img
    return np.std(gray) < BLANK_THRESHOLD / 255


def has_ui_elements(img: np.ndarray) -> bool:
    """Return True if the image has edges (UI elements like buttons, text)."""
    gray = color.rgb2gray(img) if img.ndim == 3 else img
    edges = filters.sobel(gray)
    edge_ratio = np.mean(edges > 0.1)
    return edge_ratio > EDGE_THRESHOLD


def compare_images(
    img1: np.ndarray, img2: np.ndarray
) -> tuple[float, bool]:
    """Compare two images using SSIM.  Returns ``(score, passed)``."""
    gray1 = color.rgb2gray(img1) if img1.ndim == 3 else img1
    gray2 = color.rgb2gray(img2) if img2.ndim == 3 else img2

    # Resize if dimensions don't match
    if gray1.shape != gray2.shape:
        gray2 = transform.resize(gray2, gray1.shape, anti_aliasing=True)

    score = ssim(gray1, gray2, data_range=1.0)
    return score, score >= SSIM_THRESHOLD


def verify(
    image_path: str, baseline_path: str | None = None
) -> tuple[bool, str]:
    """Verify a screenshot is valid.

    Checks that the image is not blank and contains UI elements.
    Optionally compares against a baseline using SSIM.

    Returns
    -------
    tuple[bool, str]
        ``(passed, message)``
    """
    try:
        img = io.imread(image_path)
    except Exception as e:
        return False, f"Cannot read image: {e}"

    if is_blank(img):
        return False, "Image is blank (failed render)"

    if not has_ui_elements(img):
        return False, "No UI elements detected"

    if baseline_path:
        try:
            baseline = io.imread(baseline_path)
        except Exception as e:
            return False, f"Cannot read baseline: {e}"

        score, passed = compare_images(img, baseline)
        if not passed:
            return False, f"SSIM {score:.3f} below threshold {SSIM_THRESHOLD}"
        return True, f"Screenshot matches baseline (SSIM: {score:.3f})"

    return True, "Screenshot looks valid"
