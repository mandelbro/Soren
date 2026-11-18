"""CLI wiring: discovery must get a PlaybookService (follow-up 0004).

TaskDiscovery only puts trained login/discovery flows into its navigation
prompt when it has a playbook_service. These tests fail if a CLI entry point
builds it without one, which would silently ignore trained flows.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from soren.cli import cli
from soren.models.persona import Persona
from soren.models.session import CycleReport
from soren.models.task_source import TaskSource


def _vm() -> MagicMock:
    vm = MagicMock()
    vm.vm_name = "test-vm"
    vm.ensure_ready = AsyncMock()
    vm.connect_computer = AsyncMock(return_value=MagicMock())
    return vm


def test_cycle_run_gives_discovery_the_shared_playbook_service(runner, tmp_path) -> None:
    with (
        patch("soren.services.task_backlog.get_default_db_path", return_value=tmp_path / "soren.db"),
        patch("soren.services.vm_manager.VMManager", return_value=_vm()),
        patch("soren.services.llm_service.LLMService"),
        patch("soren.services.playbook_service.PlaybookService") as playbook_cls,
        patch("soren.services.task_discovery.TaskDiscovery") as discovery_cls,
        patch("soren.services.task_executor.TaskExecutor"),
        patch("soren.services.meta_agent.SorenMetaAgent") as agent_cls,
    ):
        agent_cls.return_value.run_cycle = AsyncMock(
            return_value=CycleReport(
                cycle_id="x", cycle_type="execute", started_at="", completed_at="", vm_name="test-vm"
            )
        )
        result = runner.invoke(cli, ["cycle", "run", "--unattended"])

    assert result.exit_code == 0, result.output
    discovery_cls.assert_called_once()
    # The same instance the executor and meta-agent use, so trained flows and confidence stay consistent.
    assert discovery_cls.call_args.kwargs["playbook_service"] is playbook_cls.return_value


def test_discover_refresh_gives_discovery_a_playbook_service(runner, tmp_path) -> None:
    persona = Persona(
        id="p1",
        name="P One",
        task_sources=[TaskSource(name="acmepanel", url="https://acmepanel.example/home")],
    )
    with (
        patch("soren.services.task_backlog.get_default_db_path", return_value=tmp_path / "soren.db"),
        patch("soren.services.persona_loader.load_all", return_value=[persona]),
        patch("soren.services.vm_manager.VMManager", return_value=_vm()),
        patch("soren.services.llm_service.LLMService"),
        patch("soren.services.playbook_service.PlaybookService") as playbook_cls,
        patch("soren.services.task_discovery.TaskDiscovery") as discovery_cls,
    ):
        discovery_cls.return_value.discover = AsyncMock(return_value=[])
        result = runner.invoke(cli, ["discover", "refresh"])

    assert result.exit_code == 0, result.output
    discovery_cls.assert_called_once()
    assert discovery_cls.call_args.kwargs["playbook_service"] is playbook_cls.return_value
