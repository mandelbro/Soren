"""Baseline manager for Tier 1 OpenCV histogram comparison."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def compare_with_baseline(screenshot: bytes, baseline: bytes) -> float:
    """Compare screenshot to baseline using OpenCV histogram correlation.

    Returns similarity score 0.0-1.0. ~1ms for typical images.
    """
    img = np.frombuffer(screenshot, dtype=np.uint8)
    img = cv2.imdecode(img, cv2.IMREAD_COLOR)
    if img is None:
        return 0.0

    base = np.frombuffer(baseline, dtype=np.uint8)
    base = cv2.imdecode(base, cv2.IMREAD_COLOR)
    if base is None:
        return 0.0

    # Resize to same dimensions if needed
    if img.shape[:2] != base.shape[:2]:
        img = cv2.resize(img, (base.shape[1], base.shape[0]))

    # Compute histograms for each channel
    hist_img = [cv2.calcHist([img], [i], None, [256], [0, 256]) for i in range(3)]
    hist_base = [cv2.calcHist([base], [i], None, [256], [0, 256]) for i in range(3)]

    # Correlation for each channel, then average
    correlations = []
    for h1, h2 in zip(hist_img, hist_base):
        corr = cv2.compareHist(h1, h2, cv2.HISTCMP_CORREL)
        correlations.append(max(0.0, min(1.0, (corr + 1) / 2)))
    return sum(correlations) / 3 if correlations else 0.0


class BaselineManager:
    """Store and load baseline images for Tier 1 verification."""

    def __init__(self, base_dir: Path | str) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, name: str) -> Path:
        """Path for a baseline image file."""
        return self._base_dir / f"{name}.png"

    def store(self, name: str, image_bytes: bytes) -> Path:
        """Store baseline image. Returns path."""
        path = self.path_for(name)
        path.write_bytes(image_bytes)
        return path

    def load(self, name: str) -> bytes | None:
        """Load baseline image. Returns None if not found."""
        path = self.path_for(name)
        if not path.exists():
            return None
        return path.read_bytes()
