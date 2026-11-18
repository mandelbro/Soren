"""select_task_sources(): shared persona/source selection for `train run` and `services show`."""

from __future__ import annotations

import pytest

from soren.models.persona import Persona
from soren.models.task_source import TaskSource
from soren.services.service_resolution import SourceSelectionError, select_task_sources


def _personas() -> list[Persona]:
    return [
        Persona(name="One", id="one", task_sources=[TaskSource(name="acmepanel"), TaskSource(name="other")]),
        Persona(name="Two", id="two", task_sources=[TaskSource(name="acme-main", service="acmepanel")]),
        Persona(name="Three", id="three", task_sources=[TaskSource(name="unrelated")]),
    ]


def _names(matches) -> list[tuple[str, str]]:
    return [(p.id, s.name) for p, s in matches]


class TestSelectTaskSources:
    def test_matches_by_source_name_or_service_ref_in_load_order(self) -> None:
        assert _names(select_task_sources(_personas(), "acmepanel")) == [("one", "acmepanel"), ("two", "acme-main")]

    def test_no_match_without_persona_filter_is_empty(self) -> None:
        assert select_task_sources(_personas(), "gcal") == []

    def test_persona_filter_restricts_matches(self) -> None:
        assert _names(select_task_sources(_personas(), "acmepanel", persona_id="two")) == [("two", "acme-main")]

    def test_unknown_persona_raises(self) -> None:
        with pytest.raises(SourceSelectionError, match="'nobody' not found"):
            select_task_sources(_personas(), "acmepanel", persona_id="nobody")

    def test_persona_without_matching_source_raises(self) -> None:
        with pytest.raises(SourceSelectionError, match="'three' has no task source for 'acmepanel'"):
            select_task_sources(_personas(), "acmepanel", persona_id="three")
