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


def _gray(img: np.ndarray) -> np.ndarray:
    return color.rgb2gray(img) if img.ndim == 3 else img


def blank_std(img: np.ndarray) -> float:
    """Grayscale std on the 0-255 scale BLANK_THRESHOLD is expressed in."""
    return float(np.std(_gray(img))) * 255


def edge_ratio(img: np.ndarray) -> float:
    """Fraction of pixels on a Sobel edge (UI elements like buttons, text)."""
    return float(np.mean(filters.sobel(_gray(img)) > 0.1))


def is_blank(img: np.ndarray) -> bool:
    """Return True if the image is mostly one color (blank/failed render)."""
    return blank_std(img) < BLANK_THRESHOLD


def has_ui_elements(img: np.ndarray, edge_threshold: float | None = None) -> bool:
    """Return True if the image has edges (UI elements like buttons, text).

    ``edge_threshold`` overrides the default for sparse/dark UIs (a small card on
    a large dark background has few edges spread across the whole frame).
    """
    used = edge_threshold if edge_threshold is not None else EDGE_THRESHOLD
    return edge_ratio(img) > used


def compare_images(
    img1: np.ndarray, img2: np.ndarray, threshold: float | None = None
) -> tuple[float, bool]:
    """Compare two images using SSIM.  Returns ``(score, passed)``."""
    gray1 = _gray(img1)
    gray2 = _gray(img2)

    # Resize if dimensions don't match
    if gray1.shape != gray2.shape:
        gray2 = transform.resize(gray2, gray1.shape, anti_aliasing=True)

    used_threshold = threshold if threshold is not None else SSIM_THRESHOLD
    score = ssim(gray1, gray2, data_range=1.0)
    return score, score >= used_threshold


def verify(
    image_path: str,
    baseline_path: str | None = None,
    threshold: float | None = None,
    edge_threshold: float | None = None,
) -> tuple[bool, str, dict[str, float]]:
    """Verify a screenshot is valid.

    Checks that the image is not blank and contains UI elements.
    Optionally compares against a baseline using SSIM.

    Returns
    -------
    tuple[bool, str, dict[str, float]]
        ``(passed, message, metrics)`` -- metrics holds every score that was
        computed (blank_std, edge_ratio, and ssim when a baseline was
        compared) so failures are tunable without re-running.
    """
    metrics: dict[str, float] = {}
    try:
        img = io.imread(image_path)
    except Exception as e:
        return False, f"Cannot read image: {e}", metrics

    std = blank_std(img)
    ratio = edge_ratio(img)
    used_edge = edge_threshold if edge_threshold is not None else EDGE_THRESHOLD
    metrics["blank_std"] = round(std, 2)
    metrics["edge_ratio"] = round(ratio, 6)

    if std < BLANK_THRESHOLD:
        return False, (
            f"Image is blank/failed render"
            f" (gray std {std:.2f} < threshold {BLANK_THRESHOLD:g})"
        ), metrics

    if ratio <= used_edge:
        return False, (
            f"No UI elements detected"
            f" (edge ratio {ratio:.6f} <= threshold {used_edge:g};"
            " a sparse/dark UI may need a lower cit: edge_threshold)"
        ), metrics

    if baseline_path:
        try:
            baseline = io.imread(baseline_path)
        except Exception as e:
            return False, f"Cannot read baseline: {e}", metrics

        score, passed = compare_images(img, baseline, threshold=threshold)
        used_threshold = threshold if threshold is not None else SSIM_THRESHOLD
        metrics["ssim"] = round(float(score), 4)
        if not passed:
            return False, (
                f"SSIM {score:.3f} below threshold {used_threshold:g}"
            ), metrics
        return True, f"Screenshot matches baseline (SSIM: {score:.3f})", metrics

    return True, "Screenshot looks valid", metrics
