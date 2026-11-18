"""TaskExecutor applies the resolved service config (ADR-0002, Phase 2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.models.persona import Persona
from soren.models.site_playbook import ExecutionFlow, PlaybookStep, SiteFlows, SitePlaybook
from soren.models.task import Task
from soren.models.task_source import TaskSource
from soren.services.config_loader import SorenSettings
from soren.services.service_context import get_active_service
from soren.services.service_registry import ServiceRegistry
from soren.services.task_executor import CONFIG_ERROR_PREFIX, TaskExecutor
from tests.service_helpers import write_service

# Unit tests: fake the CUA SDK (see tests/conftest.py::fake_cua_sdk).
pytestmark = pytest.mark.usefixtures("fake_cua_sdk")

EXECUTE_METHODS = ["execute", "execute_with_learning"]


def _task() -> Task:
    return Task(
        id="t1", dedup_key="k", title="T", description="Do it", task_type="survey", source_name="gcal", persona_id="p1"
    )


def _persona() -> Persona:
    return Persona(name="P", id="p1", task_sources=[TaskSource(name="gcal", url="https://calendar.example")])


def _executor(
    tmp_path: Path,
    *,
    step_budget: int | None = None,
    playbook_service: Any = None,
    vm: Any = None,
    settings: Any = None,
) -> TaskExecutor:
    vm = vm or MagicMock()
    vm.get_tab_count = AsyncMock(side_effect=RuntimeError("no tabs in tests"))
    kwargs: dict[str, Any] = {}
    if step_budget is not None:
        kwargs["step_budget"] = step_budget
    return TaskExecutor(
        settings=settings if settings is not None else SorenSettings(),
        llm=MagicMock(),
        vm=vm,
        screenshot_dir=tmp_path / "shots",
        playbook_service=playbook_service,
        service_registry=ServiceRegistry(tmp_path / "services"),
        **kwargs,
    )


async def _run(executor: TaskExecutor, method: str, agent_run=None):
    """Run an executor method with a mocked ComputerAgent. Returns (TaskResult, agent_cls_mock, seen_services)."""
    seen: list = []

    async def default_run(*_a, **_k):
        seen.append(get_active_service())
        yield {"output": [{"role": "assistant", "content": "Task complete."}]}

    with patch("cua_agent.ComputerAgent") as agent_cls:
        agent_cls.return_value.run = agent_run or default_run
        out = await getattr(executor, method)(_task(), _persona(), MagicMock())
    result = out[0] if isinstance(out, tuple) else out
    return result, agent_cls, seen


@pytest.mark.parametrize("method", EXECUTE_METHODS)
class TestServiceContextDuringExecution:
    async def test_agent_loop_runs_inside_service_context(self, tmp_path: Path, method: str) -> None:
        write_service(tmp_path / "services", "gcal", config={"chrome_height_px": 90})
        _, _, seen = await _run(_executor(tmp_path), method)
        assert len(seen) == 1 and seen[0] is not None
        assert seen[0].name == "gcal"
        assert seen[0].config.chrome_height_px == 90
        assert get_active_service() is None

    async def test_context_cleared_even_when_agent_raises(self, tmp_path: Path, method: str) -> None:
        async def boom(*_a, **_k):
            raise RuntimeError("agent crashed")
            yield  # pragma: no cover

        result, _, _ = await _run(_executor(tmp_path), method, agent_run=boom)
        assert result.success is False
        assert get_active_service() is None

    async def test_legacy_task_without_service_file_has_unchanged_instructions(
        self, tmp_path: Path, method: str
    ) -> None:
        _, agent_cls, seen = await _run(_executor(tmp_path), method)
        instructions = agent_cls.call_args.kwargs["instructions"]
        assert "Service guidance" not in instructions
        assert seen[0].service_file is None
        assert agent_cls.call_args.kwargs["screenshot_delay"] == 0.5

    async def test_guidance_included_in_instructions(self, tmp_path: Path, method: str) -> None:
        write_service(
            tmp_path / "services",
            "gcal",
            config={"keyboard_shortcuts": [{"keys": "w", "action": "Week view"}], "instructions": ["Use the sidebar"]},
        )
        _, agent_cls, _ = await _run(_executor(tmp_path), method)
        instructions = agent_cls.call_args.kwargs["instructions"]
        assert "Service guidance for gcal:" in instructions
        assert "hotkey(key='w') — Week view" in instructions
        assert "- Use the sidebar" in instructions

    async def test_action_delay_sets_screenshot_delay(self, tmp_path: Path, method: str) -> None:
        write_service(tmp_path / "services", "gcal", config={"action_delay_ms": 250})
        _, agent_cls, _ = await _run(_executor(tmp_path), method)
        assert agent_cls.call_args.kwargs["screenshot_delay"] == 0.25


@pytest.mark.parametrize("method", EXECUTE_METHODS)
class TestStepBudget:
    @staticmethod
    async def _many_steps(*_a, **_k):
        for _ in range(100):
            yield {"output": [{"type": "computer_call", "action": {"type": "click", "x": 1, "y": 1}}]}

    async def test_service_budget_used_when_not_explicit(self, tmp_path: Path, method: str) -> None:
        write_service(tmp_path / "services", "gcal", config={"step_budget": 4})
        result, agent_cls, _ = await _run(_executor(tmp_path), method, agent_run=self._many_steps)
        assert result.steps_taken <= 4
        assert "maximum of 4 steps" in agent_cls.call_args.kwargs["instructions"]

    async def test_explicit_constructor_budget_wins(self, tmp_path: Path, method: str) -> None:
        write_service(tmp_path / "services", "gcal", config={"step_budget": 4})
        _, agent_cls, _ = await _run(_executor(tmp_path, step_budget=7), method, agent_run=self._many_steps)
        assert "maximum of 7 steps" in agent_cls.call_args.kwargs["instructions"]

    async def test_default_budget_without_service_config(self, tmp_path: Path, method: str) -> None:
        _, agent_cls, _ = await _run(_executor(tmp_path), method)
        assert "maximum of 50 steps" in agent_cls.call_args.kwargs["instructions"]


class TestPlaybookMaxStepsStillCaps:
    async def test_flow_max_steps_caps_service_budget(self, tmp_path: Path) -> None:
        write_service(tmp_path / "services", "gcal", config={"step_budget": 30})
        playbooks = MagicMock()
        playbooks.load_site.return_value = SitePlaybook(
            source_name="gcal",
            source_url="",
            flows=SiteFlows(
                execution={
                    "survey": ExecutionFlow(
                        max_steps=6, steps=[PlaybookStep(seq=1, instruction="Click", expected_after="Done")]
                    )
                }
            ),
        )
        playbooks.resolve.return_value = ""
        _, agent_cls, _ = await _run(_executor(tmp_path, playbook_service=playbooks), "execute_with_learning")
        assert "maximum of 6 steps" in agent_cls.call_args.kwargs["instructions"]


@pytest.mark.parametrize("method", EXECUTE_METHODS)
class TestConfigErrors:
    async def test_invalid_service_file_fails_without_starting_agent(self, tmp_path: Path, method: str) -> None:
        services = tmp_path / "services"
        services.mkdir()
        (services / "gcal.yaml").write_text("config:\n  step_budget: 0\n", encoding="utf-8")
        executor = _executor(tmp_path)
        with patch("cua_agent.ComputerAgent") as agent_cls:
            out = await getattr(executor, method)(_task(), _persona(), MagicMock())
        agent_cls.assert_not_called()
        result = out[0] if isinstance(out, tuple) else out
        assert result.success is False
        assert (result.error or "").startswith(CONFIG_ERROR_PREFIX)
        assert "gcal.yaml" in (result.error or "")
        if isinstance(out, tuple):
            assert out[1] is False  # never offer training for a config error

    async def test_missing_referenced_service_fails(self, tmp_path: Path, method: str) -> None:
        executor = _executor(tmp_path)
        persona = Persona(name="P", id="p1", task_sources=[TaskSource(name="gcal", service="google-calendar")])
        with patch("cua_agent.ComputerAgent") as agent_cls:
            out = await getattr(executor, method)(_task(), persona, MagicMock())
        agent_cls.assert_not_called()
        result = out[0] if isinstance(out, tuple) else out
        assert (result.error or "").startswith(CONFIG_ERROR_PREFIX)


@pytest.mark.parametrize("method", EXECUTE_METHODS)
class TestRequiredApps:
    async def test_required_apps_opened_before_agent_runs(self, tmp_path: Path, method: str) -> None:
        write_service(tmp_path / "services", "gcal", config={"required_apps": ["Calendar", "Safari"]})
        vm = MagicMock()
        order: list[str] = []
        vm.open_required_apps = AsyncMock(side_effect=lambda apps: order.append("open"))

        async def run(*_a, **_k):
            order.append("agent")
            yield {"output": [{"role": "assistant", "content": "Task complete."}]}

        await _run(_executor(tmp_path, vm=vm), method, agent_run=run)
        vm.open_required_apps.assert_awaited_once_with(["Calendar", "Safari"])
        assert order == ["open", "agent"]

    async def test_not_called_without_required_apps(self, tmp_path: Path, method: str) -> None:
        vm = MagicMock()
        vm.open_required_apps = AsyncMock()
        await _run(_executor(tmp_path, vm=vm), method)
        vm.open_required_apps.assert_not_awaited()

    async def test_open_failure_is_logged_not_fatal(self, tmp_path: Path, method: str) -> None:
        write_service(tmp_path / "services", "gcal", config={"required_apps": ["Calendar"]})
        vm = MagicMock()
        vm.open_required_apps = AsyncMock(side_effect=RuntimeError("osascript failed"))
        result, agent_cls, _ = await _run(_executor(tmp_path, vm=vm), method)
        agent_cls.assert_called_once()
        assert result.success is True


class TestMockSettingsCompatibility:
    async def test_magicmock_settings_still_work(self, tmp_path: Path) -> None:
        """Existing tests construct TaskExecutor(settings=MagicMock()); that must keep working."""
        result, agent_cls, seen = await _run(_executor(tmp_path, settings=MagicMock(), step_budget=50), "execute")
        agent_cls.assert_called_once()
        assert seen[0] is not None
