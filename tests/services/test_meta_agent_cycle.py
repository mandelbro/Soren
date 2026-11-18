"""Characterization of SorenMetaAgent.run_cycle finalization, learning (CycleLearning), and stuck paths.

Pinned before decomposing run_cycle (362 lines) into step methods and moving
post-run learning / stuck intervention into their own modules.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.models.session import TaskResult
from soren.services.cycle_learning import extract_host_domain
from tests.services.conftest import build_meta_agent

TAB_STEP = {"action_type": "tab_switch", "action_data": {"url": "https://host.example/s/1"}}


def _success(step_log=None) -> TaskResult:
    return TaskResult(success=True, steps_taken=3, step_log=step_log or [{"action_type": "click"}])


class TestSuccessLearning:
    async def test_dry_run_preview_stored_on_report(self) -> None:
        agent = build_meta_agent()
        agent._dry_run = True
        agent._playbook_service.write_back = AsyncMock(return_value=MagicMock(dry_run=True, preview="PREVIEW"))
        report = await agent.run_cycle()
        assert report.playbook_preview == "PREVIEW"
        assert agent._playbook_service.write_back.await_args.kwargs == {"dry_run": True}

    async def test_write_back_failure_does_not_fail_cycle(self) -> None:
        agent = build_meta_agent()
        agent._playbook_service.write_back = AsyncMock(side_effect=RuntimeError("llm down"))
        report = await agent.run_cycle()
        assert report.task_result is not None and report.task_result.success is True

    async def test_host_playbook_learned_for_new_domain(self) -> None:
        agent = build_meta_agent(task_result=_success([TAB_STEP]))
        agent._playbook_service.load_host.return_value = None
        host_pb = MagicMock()
        with (
            patch("soren.services.auto_playbook.auto_generate_site_playbook", AsyncMock(return_value=MagicMock())),
            patch("soren.services.auto_playbook.auto_generate_host_playbook", AsyncMock(return_value=host_pb)) as gen,
        ):
            await agent.run_cycle()
        assert gen.await_args.kwargs["host_domain"] == "host.example"
        agent._playbook_service.save_host.assert_called_once_with(host_pb)
        agent._playbook_service.update_host_confidence.assert_called_once_with("host.example", success=True)

    async def test_existing_host_playbook_not_regenerated(self) -> None:
        agent = build_meta_agent(task_result=_success([TAB_STEP]))
        agent._playbook_service.load_host.return_value = MagicMock()
        with patch("soren.services.auto_playbook.auto_generate_host_playbook", AsyncMock()) as gen:
            await agent.run_cycle()
        gen.assert_not_awaited()

    async def test_human_trained_flow_not_overwritten_by_auto_playbook(self) -> None:
        agent = build_meta_agent()
        site = MagicMock()
        site.flows.execution = {"survey": MagicMock(verified_by="human")}
        agent._playbook_service.load_site.return_value = site
        with patch("soren.services.auto_playbook.auto_generate_site_playbook", AsyncMock(return_value=MagicMock())):
            await agent.run_cycle()
        agent._playbook_service.save_flow.assert_not_called()

    async def test_memory_stored_after_success(self) -> None:
        agent = build_meta_agent()
        report = await agent.run_cycle()
        kwargs = agent._memory_service.store.await_args.kwargs
        assert kwargs["key"] == "TestSource:survey:p1"
        assert (kwargs["entry_type"], kwargs["importance"]) == ("cycle", 0.6)
        assert kwargs["value"]["summary"] == "Completed survey on TestSource; 3 steps."
        assert report.memory_entries_created == 1


class TestFailureLearning:
    async def test_failure_updates_host_confidence(self) -> None:
        agent = build_meta_agent(task_result=TaskResult(success=False, error="timeout", step_log=[TAB_STEP]))
        await agent.run_cycle()
        agent._playbook_service.update_host_confidence.assert_called_once_with("host.example", success=False)

    async def test_failure_confidence_floor_for_unattended_stuck(self) -> None:
        agent = build_meta_agent(
            task_result=TaskResult(success=False, error="Stuck detected: loop"), should_offer_training=True
        )
        with patch("soren.services.stuck_intervention.asyncio.sleep", AsyncMock()):
            await agent.run_cycle()
        assert agent._playbook_service.update_confidence.call_args.kwargs["min_confidence"] == pytest.approx(0.1)


class TestStuckPaths:
    async def test_intervention_callback_error_treated_as_skip(self) -> None:
        callbacks = MagicMock()
        callbacks.on_stuck = AsyncMock(side_effect=RuntimeError("tty closed"))
        agent = build_meta_agent(
            task_result=TaskResult(success=False, error="Stuck detected: loop"),
            should_offer_training=True,
            intervention_callbacks=callbacks,
        )
        report = await agent.run_cycle()
        [entry] = report.stuck_sources
        # Persisted in report JSON and read back by the next attended run: pin the exact shape.
        assert set(entry) == {"source_name", "stuck_reason", "skipped_at", "auto_skipped"}
        assert (entry["source_name"], entry["auto_skipped"], entry["stuck_reason"]) == (
            "TestSource",
            False,
            "Stuck detected: loop",
        )
        assert report.task_executed_id == "task-1"

    async def test_abort_returns_early_but_still_finalizes(self) -> None:
        callbacks = MagicMock()
        callbacks.on_stuck = AsyncMock(return_value="abort")
        agent = build_meta_agent(
            task_result=TaskResult(success=False, error="Stuck detected: loop", steps_taken=4),
            should_offer_training=True,
            intervention_callbacks=callbacks,
        )
        with patch("soren.services.meta_agent.write_report") as write:
            report = await agent.run_cycle()
        assert (report.task_executed_id, report.total_agent_steps) == ("task-1", 4)
        assert report.total_screenshots_captured == 0  # set only on the normal path
        write.assert_called_once()
        agent._vm.reset_browser_state.assert_awaited_once()

    async def test_unattended_stuck_auto_skips_after_timeout(self) -> None:
        agent = build_meta_agent(
            task_result=TaskResult(success=False, error="Stuck detected: loop"),
            should_offer_training=True,
            stuck_auto_skip_timeout=12.5,
        )
        with patch("soren.services.stuck_intervention.asyncio.sleep", AsyncMock()) as sleep:
            report = await agent.run_cycle()
        sleep.assert_awaited_once_with(12.5)
        assert report.stuck_detected is True
        assert report.stuck_sources[0]["auto_skipped"] is True


class TestErrorsAndFinalization:
    async def test_exception_after_selection_marks_failed(self) -> None:
        agent = build_meta_agent()
        agent._backlog.mark_completed = AsyncMock(side_effect=RuntimeError("vm connection lost"))
        report = await agent.run_cycle()
        assert report.task_result is not None and report.task_result.error == "vm connection lost"
        reason = agent._backlog.mark_failed.await_args.args[1]
        assert reason == "vm_error"

    async def test_exception_before_selection_does_not_mark_failed(self) -> None:
        agent = build_meta_agent()
        agent._backlog.get_eligible_tasks = AsyncMock(side_effect=RuntimeError("db locked"))
        report = await agent.run_cycle()
        assert report.task_result is not None and report.task_result.error == "db locked"
        agent._backlog.mark_failed.assert_not_awaited()

    async def test_finally_records_telemetry_and_resets(self) -> None:
        agent = build_meta_agent()
        agent._llm.get_telemetry.return_value = {
            "model_used": "m1",
            "fallback_triggered": 1,
            "memory_metrics": {"peak": 3},
        }
        agent._memory_service.prune = AsyncMock(return_value=7)
        agent._vm.notify_cycle_complete.return_value = True
        agent._vm.recreate_vm = AsyncMock()
        report = await agent.run_cycle()
        assert (report.model_used, report.fallback_triggered, report.memory_metrics) == ("m1", True, {"peak": 3})
        assert report.memory_entries_pruned == 7
        assert report.personas_used == ["p1"]
        assert report.completed_at and report.total_duration_seconds is not None
        agent._memory_service.clear_session.assert_called_once()
        agent._vm.reset_browser_state.assert_awaited_once()
        agent._vm.recreate_vm.assert_awaited_once()

    async def test_finally_survives_failing_bookkeeping(self) -> None:
        agent = build_meta_agent()
        agent._llm.get_telemetry.side_effect = RuntimeError("no telemetry")
        agent._memory_service.prune = AsyncMock(side_effect=RuntimeError("db"))
        agent._vm.reset_browser_state = AsyncMock(side_effect=RuntimeError("osascript"))
        agent._vm.notify_cycle_complete.side_effect = RuntimeError("x")
        report = await agent.run_cycle()
        assert report.task_result is not None and report.task_result.success is True

    async def test_no_eligible_tasks_after_discovery(self) -> None:
        agent = build_meta_agent()
        agent._backlog.get_eligible_tasks = AsyncMock(return_value=[])
        agent._discovery.discover = AsyncMock(return_value=[])
        report = await agent.run_cycle()
        assert report.discovery_ran is True
        assert report.task_executed_id is None
        assert agent._backlog.get_summary.await_count == 2  # pins existing behavior: early path + finally


class TestExtractHostDomain:
    @pytest.mark.parametrize(
        ("step_log", "expected"),
        [
            ([{"action_type": "new_tab", "action_data": {"tab_url": "https://a.example/x"}}], "a.example"),
            ([{"action_type": "open_tab", "action_data": {"url": "https://b.example"}}], "b.example"),
            ([{"action_type": "click", "action_data": {"url": "https://c.example"}}], None),
            ([{"action_type": "tab_switch", "action_data": "not a dict"}], None),
            ([{"action_type": "tab_switch", "action_data": {"url": "no-scheme.example"}}], None),
            ([{"action_type": "tab_switch", "action_data": {"domain": "d.example"}}, TAB_STEP], "host.example"),
        ],
    )
    def test_extract(self, step_log, expected) -> None:
        assert extract_host_domain(step_log) == expected


class TestEdgeCases:
    async def test_executor_crash_is_a_failure_not_stuck(self) -> None:
        agent = build_meta_agent()
        agent._executor.execute_with_learning = AsyncMock(side_effect=RuntimeError("agent crashed"))
        with patch("soren.services.stuck_intervention.asyncio.sleep", AsyncMock()) as sleep:
            report = await agent.run_cycle()
        assert report.task_result is not None and report.task_result.error == "agent crashed"
        assert (report.stuck_detected, report.stuck_sources) == (False, [])
        sleep.assert_not_awaited()
        agent._backlog.mark_failed.assert_awaited_once()

    async def test_no_personas_skips_vm_and_browser_reset(self) -> None:
        agent = build_meta_agent()
        agent._persona_loader = MagicMock(return_value=[])
        with patch("soren.services.meta_agent.write_report") as write:
            report = await agent.run_cycle()
        agent._vm.connect_computer.assert_not_awaited()
        agent._vm.reset_browser_state.assert_not_awaited()
        write.assert_called_once()
        assert report.task_executed_id is None

    async def test_highest_score_selected_ties_oldest_first(self) -> None:
        from tests.services.conftest import make_task

        agent = build_meta_agent()
        low, high_new, high_old = make_task("low"), make_task("high-new"), make_task("high-old")
        high_new.created_at, high_old.created_at = "2025-02-01T00:00:00Z", "2025-01-01T00:00:00Z"
        agent._backlog.get_eligible_tasks = AsyncMock(return_value=[low, high_new, high_old])
        agent._scorer.score.side_effect = lambda t, *a: {"low": 0.1, "high-new": 0.9, "high-old": 0.9}[t.id]
        report = await agent.run_cycle()
        assert report.task_executed_id == "high-old"
