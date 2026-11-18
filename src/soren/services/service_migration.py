"""Migrate legacy inline persona task_source values into config/services/<name>.yaml (ADR-0002 Phase 5).

Only service *files* are created: persona files are never modified, and
``settings.yaml`` ``services.<name>`` overrides are never moved (they are
operator overrides that rank above service files). Because explicit persona
values still outrank the new file, every persona that supplied a value
resolves exactly as before. The plan lists the one visible change: personas
that did not set a value another persona did will now inherit it from the file.

Services whose personas disagree on a value are reported as conflicts and not
written. Existing service files are never overwritten.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from soren.models.persona import Persona
from soren.models.service import Service, ServiceConfig
from soren.models.task_source import TaskSource
from soren.services.service_registry import ServiceRegistry
from soren.services.service_resolution import explicit_persona_values
from soren.services.service_writer import ServiceWriter, ServiceWriteResult

_TOP_LEVEL_FIELDS = ("type", "url", "discovery_hint")


@dataclass(frozen=True)
class SourceRef:
    persona_id: str
    source_name: str

    def __str__(self) -> str:
        return f"{self.persona_id}:{self.source_name}"


@dataclass(frozen=True)
class PlannedService:
    path: Path
    service: Service
    sources: list[SourceRef]


@dataclass(frozen=True)
class MigrationConflict:
    name: str
    field: str
    values: dict[str, Any]  # "persona:source" -> value


@dataclass(frozen=True)
class InheritNotice:
    name: str
    source: SourceRef
    field: str
    value: Any


@dataclass
class MigrationPlan:
    creates: list[PlannedService] = field(default_factory=list)
    existing: list[tuple[str, Path]] = field(default_factory=list)
    conflicts: list[MigrationConflict] = field(default_factory=list)
    inherits: list[InheritNotice] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.creates or self.existing or self.conflicts)


def plan_migration(personas: list[Persona], registry: ServiceRegistry, persona_id: str | None = None) -> MigrationPlan:
    """Plan which service files to create from persona inline values.

    Conflicts and inheritance are checked across *all* personas; ``persona_id``
    only limits which services are created. Raises ValueError for an unknown persona.
    """
    if persona_id is not None and not any(p.id == persona_id for p in personas):
        raise ValueError(f"Persona {persona_id!r} not found.")

    groups: dict[str, list[tuple[SourceRef, TaskSource]]] = defaultdict(list)
    for persona in personas:
        for source in persona.task_sources:
            groups[source.service_key].append((SourceRef(persona.id, source.name), source))

    wanted = set(groups)
    if persona_id is not None:
        wanted = {s.service_key for p in personas if p.id == persona_id for s in p.task_sources}

    plan = MigrationPlan()
    for name in sorted(wanted):
        members = [(ref, explicit_persona_values(src)) for ref, src in groups[name]]
        if not any(values for _, values in members):
            continue  # nothing inline to migrate
        path = registry.path_for(name)
        if path.is_file():
            plan.existing.append((name, path))
            continue
        merged, conflicts = _merge(name, members)
        if conflicts:
            plan.conflicts.extend(conflicts)
            continue
        contributors = [ref for ref, values in members if values]
        plan.creates.append(PlannedService(path=path, service=_to_service(name, merged), sources=contributors))
        plan.inherits.extend(
            InheritNotice(name, ref, f, merged[f]) for ref, values in members for f in sorted(merged) if f not in values
        )
    return plan


def apply_migration(plan: MigrationPlan, writer: ServiceWriter) -> list[ServiceWriteResult]:
    """Write every planned service file (validated, atomic; existing files are not in the plan)."""
    return [writer.write(planned.service) for planned in plan.creates]


def _merge(
    name: str, members: list[tuple[SourceRef, dict[str, Any]]]
) -> tuple[dict[str, Any], list[MigrationConflict]]:
    by_field: dict[str, dict[str, Any]] = defaultdict(dict)
    for ref, values in members:
        for field_name, value in values.items():
            by_field[field_name][str(ref)] = value
    merged: dict[str, Any] = {}
    conflicts: list[MigrationConflict] = []
    for field_name in sorted(by_field):
        values = by_field[field_name]
        distinct = {repr(v) for v in values.values()}
        if len(distinct) > 1:
            conflicts.append(MigrationConflict(name, field_name, values))
        else:
            merged[field_name] = next(iter(values.values()))
    return merged, conflicts


def _to_service(name: str, merged: dict[str, Any]) -> Service:
    top = {f: merged[f] for f in _TOP_LEVEL_FIELDS if f in merged}
    config = {f: v for f, v in merged.items() if f not in _TOP_LEVEL_FIELDS and f in ServiceConfig.model_fields}
    return Service.model_validate({"name": name, **top, "config": config})
