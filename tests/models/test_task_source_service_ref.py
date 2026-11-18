"""Tests for TaskSource.service (persona -> config/services binding) and legacy compatibility."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from soren.models.task_source import TaskSource
from soren.services.persona_loader import load_persona

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestServiceKey:
    def test_legacy_source_without_service_uses_name(self) -> None:
        src = TaskSource.model_validate({"name": "acmepanel", "url": "https://acmepanel.example"})
        assert src.service is None
        assert src.service_key == "acmepanel"

    def test_service_reference_sets_key(self) -> None:
        src = TaskSource.model_validate({"name": "acme-main", "service": "acmepanel"})
        assert src.service == "acmepanel"
        assert src.service_key == "acmepanel"
        assert src.name == "acme-main"

    def test_binding_without_name_takes_name_from_service(self) -> None:
        src = TaskSource.model_validate({"service": "acmepanel", "credential_ref": "acmepanel"})
        assert src.name == "acmepanel"
        assert src.service_key == "acmepanel"

    @pytest.mark.parametrize("data", [{}, {"service": ""}, {"name": None, "service": " "}])
    def test_neither_name_nor_service_rejected(self, data: dict) -> None:
        with pytest.raises(ValidationError):
            TaskSource.model_validate(data)

    @pytest.mark.parametrize("data", [{"name": ""}, {"name": " ", "service": " "}])
    def test_blank_legacy_name_still_loads_as_before(self, data: dict) -> None:
        """main accepted a blank name; rejecting it now would stop the whole persona file loading."""
        src = TaskSource.model_validate(data)
        assert src.name == data["name"]
        assert src.service is None

    def test_non_blank_legacy_name_is_never_rewritten(self) -> None:
        """task.source_name matching depends on the exact legacy name; only blank names are filled."""
        src = TaskSource.model_validate({"name": " Acme Panel ", "service": " acmepanel "})
        assert src.name == " Acme Panel "
        assert src.service == "acmepanel"

    def test_defaults_unchanged_for_legacy_fields(self) -> None:
        """Adding `service` must not change legacy defaults consumed elsewhere."""
        src = TaskSource.model_validate({"name": "x"})
        assert (src.type, src.url, src.typing_delay_ms, src.action_delay_ms, src.cooldown_between_tasks_s) == (
            "survey_platform",
            "",
            50,
            500,
            30,
        )


class TestShippedExamplePersona:
    def test_example_persona_binds_to_the_acmepanel_service(self) -> None:
        persona = load_persona(REPO_ROOT / "config" / "personas" / "example.yaml")
        [source] = persona.task_sources
        assert source.service == "acmepanel"
        assert source.name == "acmepanel"  # defaults to the service name; tasks match on it
        assert source.credential_ref == "acmepanel"  # credentials stay persona-bound
