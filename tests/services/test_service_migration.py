"""Migration of legacy inline persona task_source values into config/services/<name>.yaml (Phase 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from soren.models.persona import Persona
from soren.models.task_source import TaskSource
from soren.services.config_loader import SorenSettings
from soren.services.service_migration import apply_migration, plan_migration
from soren.services.service_registry import ServiceRegistry, load_service_file
from soren.services.service_resolution import resolve_service
from soren.services.service_writer import ServiceWriter
from tests.service_helpers import write_service


def _persona(pid: str, *sources: TaskSource) -> Persona:
    return Persona(name=pid.title(), id=pid, task_sources=list(sources))


def _registry(tmp_path: Path) -> ServiceRegistry:
    return ServiceRegistry(tmp_path / "services")


ACME = {"name": "acmepanel", "type": "survey_platform", "url": "https://acme.example", "discovery_hint": "Studies tab"}


class TestPlan:
    def test_inline_values_become_a_planned_service(self, tmp_path: Path) -> None:
        personas = [_persona("a", TaskSource(**ACME, typing_delay_ms=80, credential_ref="acme"))]
        plan = plan_migration(personas, _registry(tmp_path))
        [planned] = plan.creates
        assert planned.path == tmp_path / "services" / "acmepanel.yaml"
        svc = planned.service
        assert (svc.name, svc.type, svc.url, svc.discovery_hint) == (
            "acmepanel",
            "survey_platform",
            "https://acme.example",
            "Studies tab",
        )
        assert svc.config.set_values() == {"typing_delay_ms": 80}
        assert [str(s) for s in planned.sources] == ["a:acmepanel"]

    def test_defaults_and_blanks_are_not_migrated(self, tmp_path: Path) -> None:
        # type/delays left at model defaults and blank strings are not explicit values.
        personas = [_persona("a", TaskSource(name="acmepanel", url="https://acme.example", discovery_hint=""))]
        [planned] = plan_migration(personas, _registry(tmp_path)).creates
        assert planned.service.model_dump(exclude_none=True) == {
            "version": 1,
            "name": "acmepanel",
            "url": "https://acme.example",
            "config": {},
        }

    def test_identical_values_across_personas_merge(self, tmp_path: Path) -> None:
        personas = [_persona("a", TaskSource(**ACME)), _persona("b", TaskSource(**ACME))]
        plan = plan_migration(personas, _registry(tmp_path))
        [planned] = plan.creates
        assert [str(s) for s in planned.sources] == ["a:acmepanel", "b:acmepanel"]
        assert plan.conflicts == [] and plan.inherits == []

    def test_conflicting_values_reported_and_service_not_planned(self, tmp_path: Path) -> None:
        personas = [
            _persona("a", TaskSource(name="acmepanel", url="https://one.example")),
            _persona("b", TaskSource(name="acmepanel", url="https://two.example")),
        ]
        plan = plan_migration(personas, _registry(tmp_path))
        assert plan.creates == []
        [conflict] = plan.conflicts
        assert (conflict.name, conflict.field) == ("acmepanel", "url")
        assert conflict.values == {"a:acmepanel": "https://one.example", "b:acmepanel": "https://two.example"}

    def test_existing_service_file_is_never_overwritten(self, tmp_path: Path) -> None:
        write_service(tmp_path / "services", "acmepanel", url="https://file.example")
        plan = plan_migration([_persona("a", TaskSource(**ACME))], _registry(tmp_path))
        assert plan.creates == []
        assert plan.existing == [("acmepanel", tmp_path / "services" / "acmepanel.yaml")]

    def test_sources_without_inline_values_are_skipped(self, tmp_path: Path) -> None:
        personas = [_persona("a", TaskSource(name="acmepanel", credential_ref="acme"))]
        plan = plan_migration(personas, _registry(tmp_path))
        assert plan.creates == [] and plan.is_empty

    def test_inherit_notice_when_another_persona_lacks_the_value(self, tmp_path: Path) -> None:
        personas = [_persona("a", TaskSource(**ACME)), _persona("b", TaskSource(name="acmepanel", credential_ref="b"))]
        plan = plan_migration(personas, _registry(tmp_path))
        assert len(plan.creates) == 1
        fields = sorted(n.field for n in plan.inherits)
        assert fields == ["discovery_hint", "type", "url"]
        assert {str(n.source) for n in plan.inherits} == {"b:acmepanel"}

    def test_binding_form_source_groups_by_service_name(self, tmp_path: Path) -> None:
        personas = [_persona("a", TaskSource(name="acme-main", service="acmepanel", url="https://acme.example"))]
        [planned] = plan_migration(personas, _registry(tmp_path)).creates
        assert planned.service.name == "acmepanel"
        assert [str(s) for s in planned.sources] == ["a:acme-main"]

    def test_persona_filter_limits_created_services_but_checks_everyone(self, tmp_path: Path) -> None:
        personas = [
            _persona(
                "a", TaskSource(name="acmepanel", url="https://one.example"), TaskSource(name="gcal", url="https://cal")
            ),
            _persona(
                "b", TaskSource(name="acmepanel", url="https://two.example"), TaskSource(name="other", url="https://o")
            ),
        ]
        plan = plan_migration(personas, _registry(tmp_path), persona_id="a")
        assert [p.service.name for p in plan.creates] == ["gcal"]
        assert [c.name for c in plan.conflicts] == ["acmepanel"]  # b's disagreeing value still detected

    def test_unknown_persona_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="nobody"):
            plan_migration([_persona("a", TaskSource(**ACME))], _registry(tmp_path), persona_id="nobody")


class TestApply:
    def test_writes_planned_services(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path)
        plan = plan_migration([_persona("a", TaskSource(**ACME, action_delay_ms=250))], registry)
        [result] = apply_migration(plan, ServiceWriter(registry))
        assert result.created is True
        written = load_service_file(result.path)
        assert written.url == "https://acme.example"
        assert written.config.action_delay_ms == 250

    def test_resolution_unchanged_for_every_contributing_persona(self, tmp_path: Path) -> None:
        """The migration guarantee: sources that supplied values resolve exactly as before."""
        registry = _registry(tmp_path)
        settings = SorenSettings.model_validate({"services": {"acmepanel": {"step_budget": 7}}})
        personas = [
            _persona("a", TaskSource(**ACME, typing_delay_ms=80)),
            _persona("b", TaskSource(**ACME, typing_delay_ms=80)),
            _persona("c", TaskSource(name="gcal", url="https://cal.example", type="generic_form")),
        ]

        def resolve_all() -> list[dict]:
            out = []
            for persona in personas:
                for source in persona.task_sources:
                    r = resolve_service(
                        source.service_key, registry=registry, settings=settings, source=source, environ={}
                    )
                    out.append(r.model_dump(exclude={"service_file", "provenance"}))
            return out

        before = resolve_all()
        apply_migration(plan_migration(personas, registry), ServiceWriter(registry))
        assert len(registry.list()) == 2
        assert resolve_all() == before

    def test_settings_services_overrides_are_not_moved(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path)
        apply_migration(plan_migration([_persona("a", TaskSource(**ACME))], registry), ServiceWriter(registry))
        # step_budget lives only in settings.yaml services.<name>; the migrated file must not contain it.
        assert load_service_file(registry.path_for("acmepanel")).config.step_budget is None
