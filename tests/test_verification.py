"""Tests for verification system."""

import cv2
import numpy as np
import pytest

from soren.verification.baseline import BaselineManager, compare_with_baseline
from soren.verification.verifier import (
    ActionVerifier,
    VerificationResult,
    _parse_vlm_response,
)


def _make_png(width: int = 100, height: int = 100, color: tuple = (128, 128, 128)) -> bytes:
    """Create a simple PNG as bytes."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = color
    _, buf = cv2.imencode(".png", img)
    return buf.tobytes()


class TestBaselineComparison:
    """Tier 1 baseline comparison tests."""

    def test_identical_images_high_similarity(self) -> None:
        """Tier 1 returns high similarity for identical images."""
        png = _make_png(100, 100, (50, 100, 150))
        sim = compare_with_baseline(png, png)
        assert sim >= 0.99

    def test_different_images_low_similarity(self) -> None:
        """Tier 1 returns low similarity for different images."""
        png1 = _make_png(100, 100, (0, 0, 0))
        png2 = _make_png(100, 100, (255, 255, 255))
        sim = compare_with_baseline(png1, png2)
        assert sim < 0.5


class TestVerifierThreshold:
    """Tier 1 threshold gates Tier 2 escalation."""

    @pytest.mark.asyncio
    async def test_below_threshold_escalates_to_tier2(self, tmp_path) -> None:
        """Below threshold escalates to Tier 2."""
        base_dir = tmp_path / "baselines"
        base_dir.mkdir()
        bm = BaselineManager(base_dir)
        # Store a baseline
        baseline_png = _make_png(100, 100, (100, 100, 100))
        bm.store("test", baseline_png)
        # Different screenshot - low similarity
        different_png = _make_png(100, 100, (200, 50, 50))
        verifier = ActionVerifier(bm, llm_service=None, threshold=0.85)
        result = await verifier.verify(different_png, "form submitted", baseline_name="test")
        assert result.tier_used == 2 or (result.tier_used == 1 and not result.passed)
        # Without LLM, tier 2 fails
        if result.tier_used == 2:
            assert not result.passed

    @pytest.mark.asyncio
    async def test_above_threshold_accepts(self, tmp_path) -> None:
        """Above threshold accepts without Tier 2."""
        base_dir = tmp_path / "baselines"
        base_dir.mkdir()
        bm = BaselineManager(base_dir)
        png = _make_png(100, 100)
        bm.store("test", png)
        verifier = ActionVerifier(bm, llm_service=None, threshold=0.5)
        result = await verifier.verify(png, "any state", baseline_name="test")
        assert result.tier_used == 1
        assert result.passed
        assert result.confidence >= 0.5


class TestTier2Parsing:
    """Tier 2 VLM response parsing."""

    def test_parses_pass(self) -> None:
        """Tier 2 parses PASS from response."""
        passed, _ = _parse_vlm_response("PASS - looks good")
        assert passed is True

    def test_parses_fail(self) -> None:
        """Tier 2 parses FAIL from response."""
        passed, _ = _parse_vlm_response("FAIL - form not submitted")
        assert passed is False

    def test_verification_result_captures_tier_and_duration(self) -> None:
        """VerificationResult captures tier used and duration."""
        r = VerificationResult(passed=True, confidence=0.9, tier_used=2, duration_ms=1234.5)
        assert r.tier_used == 2
        assert r.duration_ms == 1234.5


class TestBaselineManager:
    """BaselineManager store/load."""

    def test_store_and_load(self, tmp_path) -> None:
        """Store and load baseline."""
        bm = BaselineManager(tmp_path)
        png = _make_png(50, 50)
        path = bm.store("my_baseline", png)
        assert path.exists()
        loaded = bm.load("my_baseline")
        assert loaded == png

    def test_load_missing_returns_none(self, tmp_path) -> None:
        """Load missing baseline returns None."""
        bm = BaselineManager(tmp_path)
        assert bm.load("nonexistent") is None
