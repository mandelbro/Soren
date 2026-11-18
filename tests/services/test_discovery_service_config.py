"""TaskDiscovery applies the resolved service config (ADR-0002, Phase 2)."""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.adapters.base import DISCOVERY_READY
from soren.models.persona import Persona
from soren.models.task_source import TaskSource
from soren.services.config_loader import SorenSettings
from soren.services.service_context import get_active_service
from soren.services.service_registry import ServiceRegistry
from soren.services.task_discovery import TaskDiscovery
from tests.service_helpers import write_service

# Unit tests: fake the CUA SDK (see tests/conftest.py::fake_cua_sdk).
pytestmark = pytest.mark.usefixtures("fake_cua_sdk")

PNG_B64 = base64.b64encode(b"png").decode()


def _discovery(tmp_path: Path, llm: Any = None) -> TaskDiscovery:
    return TaskDiscovery(
        SorenSettings(),
        llm or AsyncMock(),
        AsyncMock(),
        AsyncMock(),
        screenshot_dir=tmp_path / "shots",
        service_registry=ServiceRegistry(tmp_path / "services"),
    )


def _agent_run(seen: list):
    async def run(*_a, **_k):
        seen.append(get_active_service())
        yield {
            "output": [{"type": "computer_call_output", "output": {"image_url": f"data:image/png;base64,{PNG_B64}"}}]
        }
        yield {"output": [{"role": "assistant", "content": DISCOVERY_READY}]}

    return run


def _llm_returning_one_task() -> AsyncMock:
    llm = AsyncMock()
    llm.generate = AsyncMock(side_effect=["raw extracted text", '[{"title": "Survey 1"}]'])
    return llm


class TestBindingFormSource:
    async def test_service_file_supplies_url_type_and_hint(self, tmp_path: Path) -> None:
        write_service(
            tmp_path / "services",
            "acmepanel",
            type="survey_platform",
            url="https://acmepanel.example/home",
            discovery_hint="Look at the Studies tab",
        )
        source = TaskSource(service="acmepanel", credential_ref="acmepanel")  # no url/type inline
        seen: list = []
        with patch("cua_agent.ComputerAgent") as agent_cls:
            agent_cls.return_value.run = _agent_run(seen)
            tasks = await _discovery(tmp_path, _llm_returning_one_task()).discover(
                Persona(name="P", id="p1"), source, MagicMock()
            )
        nav_prompt = agent_cls.call_args.kwargs["instructions"]
        assert "https://acmepanel.example/home" in nav_prompt
        assert "Look at the Studies tab" in nav_prompt
        assert [t.source_url for t in tasks] == ["https://acmepanel.example/home"]
        assert tasks[0].source_name == "acmepanel"
        assert tasks[0].source_type == "survey_platform"

    async def test_adapter_selected_by_resolved_type(self, tmp_path: Path) -> None:
        write_service(tmp_path / "services", "inbox", type="email", url="https://mail.example")
        source = TaskSource(service="inbox")  # TaskSource.type defaults to survey_platform
        with patch("soren.services.task_discovery.get_adapter", return_value=None) as get_adapter:
            assert await _discovery(tmp_path).discover(Persona(name="P"), source, MagicMock()) == []
        get_adapter.assert_called_once_with("email")

    async def test_explicit_persona_values_still_win(self, tmp_path: Path) -> None:
        write_service(tmp_path / "services", "acmepanel", url="https://file.example")
        source = TaskSource(name="acmepanel", url="https://persona.example")
        seen: list = []
        with patch("cua_agent.ComputerAgent") as agent_cls:
            agent_cls.return_value.run = _agent_run(seen)
            tasks = await _discovery(tmp_path, _llm_returning_one_task()).discover(
                Persona(name="P"), source, MagicMock()
            )
        assert "https://persona.example" in agent_cls.call_args.kwargs["instructions"]
        assert tasks[0].source_url == "https://persona.example"


class TestServiceContextDuringDiscovery:
    async def test_navigation_runs_inside_service_context(self, tmp_path: Path) -> None:
        write_service(tmp_path / "services", "acmepanel", url="https://a.example", config={"chrome_height_px": 100})
        seen: list = []
        with patch("cua_agent.ComputerAgent") as agent_cls:
            agent_cls.return_value.run = _agent_run(seen)
            await _discovery(tmp_path, _llm_returning_one_task()).discover(
                Persona(name="P"), TaskSource(name="acmepanel"), MagicMock()
            )
        assert seen and seen[0] is not None and seen[0].config.chrome_height_px == 100
        assert get_active_service() is None

    async def test_legacy_source_without_file_unchanged(self, tmp_path: Path) -> None:
        source = TaskSource(name="betapanel", type="survey_platform", url="https://x.co")
        seen: list = []
        with patch("cua_agent.ComputerAgent") as agent_cls:
            agent_cls.return_value.run = _agent_run(seen)
            tasks = await _discovery(tmp_path, _llm_returning_one_task()).discover(
                Persona(name="P"), source, MagicMock()
            )
        assert "https://x.co" in agent_cls.call_args.kwargs["instructions"]
        assert tasks[0].source_url == "https://x.co"


class TestConfigErrors:
    @pytest.mark.parametrize(
        ("files", "source_kwargs"),
        [
            ({"acmepanel.yaml": "config:\n  step_budget: 0\n"}, {"name": "acmepanel"}),
            ({}, {"service": "missing"}),
        ],
    )
    async def test_broken_source_skipped_and_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, files: dict[str, str], source_kwargs: dict
    ) -> None:
        services = tmp_path / "services"
        services.mkdir()
        for name, text in files.items():
            (services / name).write_text(text, encoding="utf-8")
        with (
            patch("cua_agent.ComputerAgent") as agent_cls,
            caplog.at_level(logging.ERROR, logger="soren.services.task_discovery"),
        ):
            tasks = await _discovery(tmp_path).discover(Persona(name="P"), TaskSource(**source_kwargs), MagicMock())
        assert tasks == []
        agent_cls.assert_not_called()
        assert any("service config" in r.getMessage().lower() for r in caplog.records)


class TestUnexpectedErrors:
    async def test_pipeline_error_is_logged_not_swallowed(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A crash inside the pipeline still returns [] (other sources continue) but is logged with a traceback."""
        discovery = _discovery(tmp_path)
        with (
            patch.object(discovery, "_phase1_navigate", AsyncMock(side_effect=RuntimeError("vnc gone"))),
            caplog.at_level(logging.ERROR, logger="soren.services.task_discovery"),
        ):
            tasks = await discovery.discover(
                Persona(name="P", id="p1"), TaskSource(name="betapanel", url="https://x.co"), MagicMock()
            )
        assert tasks == []
        [record] = [r for r in caplog.records if "betapanel" in r.getMessage()]
        assert "p1" in record.getMessage()
        assert record.exc_info is not None and "vnc gone" in str(record.exc_info[1])
