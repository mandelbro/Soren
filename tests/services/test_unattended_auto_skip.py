"""Tests for unattended mode auto-skip with configurable timeout (Task 023).

Verifies the auto-skip behavior when running in unattended mode:
- Stuck sources auto-skipped after timeout
- Attended mode shows prompt instead
- stuck_skipped logged in cycle report
- stuck_sources list populated correctly
- Next attended run warns about previously skipped sources
- Confidence floor (0.1) enforced during unattended auto-skip
- Configurable timeout value respected
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.models.session import CycleReport, TaskResult
from tests.services.conftest import build_meta_agent


class _StubCallbacks:
    """Test stub for intervention callbacks (attended mode)."""

    def __init__(self, response: Literal["train", "skip", "abort"] = "skip") -> None:
        self._response = response

    async def on_stuck(
        self,
        source_name: str,
        stuck_reason: str,
        cycle_context: dict[str, Any],
    ) -> Literal["train", "skip", "abort"]:
        return self._response


class TestUnattendedAutoSkipAfterTimeout:
    """Unattended mode auto-skips stuck source after timeout."""

    @pytest.mark.asyncio
    async def test_auto_skip_after_timeout(self) -> None:
        agent = build_meta_agent(
            should_offer_training=True,
            task_result=TaskResult(success=False, error="Stuck: same screenshot 3 times"),
            intervention_callbacks=None,
        )

        with patch("soren.services.meta_agent.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            report = await agent.run_cycle()

        assert report.stuck_detected is True
        assert len(report.stuck_sources) == 1
        stuck = report.stuck_sources[0]
        assert stuck["source_name"] == "TestSource"
        assert stuck["auto_skipped"] is True
        mock_sleep.assert_called_once_with(0.01)


class TestAttendedModeShowsPromptNotAutoSkip:
    """Attended mode (with callbacks) shows prompt instead of auto-skip."""

    @pytest.mark.asyncio
    async def test_attended_mode_does_not_auto_skip(self) -> None:
        callbacks = _StubCallbacks(response="skip")
        agent = build_meta_agent(
            should_offer_training=True,
            task_result=TaskResult(success=False, error="Stuck: same screenshot 3 times"),
            intervention_callbacks=callbacks,
        )

        with patch("soren.services.meta_agent.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            report = await agent.run_cycle()

        # asyncio.sleep should NOT be called (no timeout wait)
        mock_sleep.assert_not_called()
        # But stuck was still detected
        assert report.stuck_detected is True


class TestStuckSourcesListPopulated:
    """stuck_sources list contains correct source info."""

    @pytest.mark.asyncio
    async def test_stuck_sources_populated(self) -> None:
        agent = build_meta_agent(
            should_offer_training=True,
            task_result=TaskResult(success=False, error="Stuck: same screenshot 3 times"),
            intervention_callbacks=None,
        )

        with patch("soren.services.meta_agent.asyncio.sleep", new_callable=AsyncMock):
            report = await agent.run_cycle()

        assert len(report.stuck_sources) == 1
        entry = report.stuck_sources[0]
        assert entry["source_name"] == "TestSource"
        assert "stuck_reason" in entry
        assert "skipped_at" in entry
        assert entry["auto_skipped"] is True


class TestConfidenceFloorEnforced:
    """Confidence floor (0.1) enforced during unattended auto-skip."""

    @pytest.mark.asyncio
    async def test_confidence_floor_passed_to_update(self) -> None:
        playbook_service = MagicMock()
        playbook_service.write_back = AsyncMock(return_value=MagicMock(dry_run=False, preview=None))
        playbook_service.load_site.return_value = None
        playbook_service.save_flow.return_value = MagicMock()
        playbook_service.update_confidence = MagicMock()
        playbook_service.update_host_confidence = MagicMock()

        agent = build_meta_agent(
            should_offer_training=True,
            task_result=TaskResult(success=False, error="Stuck: same screenshot 3 times"),
            intervention_callbacks=None,
            playbook_service=playbook_service,
        )

        with patch("soren.services.meta_agent.asyncio.sleep", new_callable=AsyncMock):
            await agent.run_cycle()

        playbook_service.update_confidence.assert_called_once()
        call_kwargs = playbook_service.update_confidence.call_args
        assert call_kwargs[1].get("min_confidence") == 0.1


class TestConfigurableTimeoutRespected:
    """Configurable timeout value is respected."""

    @pytest.mark.asyncio
    async def test_custom_timeout(self) -> None:
        agent = build_meta_agent(
            should_offer_training=True,
            task_result=TaskResult(success=False, error="Stuck: same screenshot 3 times"),
            intervention_callbacks=None,
            stuck_auto_skip_timeout=5.0,
        )

        with patch("soren.services.meta_agent.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await agent.run_cycle()

        mock_sleep.assert_called_once_with(5.0)


class TestPreviousStuckSourceWarning:
    """Next attended run warns about previously stuck-skipped sources."""

    @pytest.mark.asyncio
    async def test_warns_about_previous_stuck_sources(self, tmp_path: Path) -> None:
        # Create a previous report with stuck sources
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        prev_report = CycleReport(
            cycle_id="prev-123",
            stuck_sources=[
                {
                    "source_name": "PreviousSource",
                    "stuck_reason": "Action loop",
                    "skipped_at": "2025-01-01T00:00:00Z",
                    "auto_skipped": True,
                }
            ],
        )
        report_path = report_dir / "prev-123.json"
        report_path.write_text(prev_report.model_dump_json(indent=2))
        latest = report_dir / "latest.json"
        latest.symlink_to(report_path.name)

        callbacks = _StubCallbacks(response="skip")
        agent = build_meta_agent(
            should_offer_training=False,
            task_result=TaskResult(success=True, steps_taken=3),
            intervention_callbacks=callbacks,
            report_dir=report_dir,
        )

        with patch("soren.services.meta_agent.logger") as mock_logger:
            await agent.run_cycle()

            warning_calls = [call for call in mock_logger.warning.call_args_list if "PreviousSource" in str(call)]
            assert len(warning_calls) >= 1
