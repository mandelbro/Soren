"""`train run` target resolution: which playbook key, URL, and service config to train (ADR-0002)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import click
from rich.console import Console
from rich.markup import escape

from soren.models.persona import Persona
from soren.services.config_loader import SorenSettings
from soren.services.service_registry import ServiceRegistry
from soren.services.service_resolution import (
    ResolvedService,
    SourceSelectionError,
    resolve_service,
    select_task_sources,
)


@dataclass(frozen=True)
class TrainingTarget:
    """What `train run SOURCE` trains: playbook key, auto-navigation URL, and service config."""

    source_name: str
    url: str
    url_origin: str
    service: ResolvedService
    persona: Persona | None


def resolve_training_target(
    source_arg: str,
    *,
    url_flag: str | None,
    personas: list[Persona],
    persona_id: str | None,
    settings: SorenSettings,
    registry: ServiceRegistry,
    environ: Mapping[str, str] | None = None,
) -> TrainingTarget:
    """Resolve SOURCE into a training target (ADR-0002).

    ``source_arg`` matches a persona task source by name *or* by its
    ``service:`` reference. Candidates are scanned in load order (or only the
    ``--persona`` one); the first whose resolved URL is non-empty wins, else the
    first match. With no match, ``source_arg`` is resolved as a service name.

    URL precedence: ``--url`` > resolved service URL (explicit persona value >
    ``config/services/<name>.yaml``) > "" (TrainingSession skips auto-navigation).
    The playbook key is the matched source's name, which is what execution
    looks up. Raises click.ClickException for an unknown persona and
    ServiceConfigError for invalid service config.
    """
    try:
        matches = select_task_sources(personas, source_arg, persona_id)
    except SourceSelectionError as exc:
        raise click.ClickException(str(exc))

    # Resolve lazily: stop at the first match whose service has a URL, so a
    # broken service file on an unrelated later persona never blocks training.
    resolved: list[tuple[Persona | None, str, ResolvedService]] = []
    for persona, source in matches:
        service = resolve_service(
            source.service_key,
            registry=registry,
            settings=settings,
            source=source,
            persona_id=persona.id or None,
            environ=environ,
        )
        resolved.append((persona, source.name, service))
        if service.url:
            break
    if not resolved:
        service = resolve_service(source_arg, registry=registry, settings=settings, environ=environ)
        resolved.append((None, source_arg, service))

    # Only the last candidate can have a URL (the loop stops there); otherwise use the first match.
    persona, source_name, service = resolved[-1] if resolved[-1][2].url else resolved[0]
    if url_flag:
        url, origin = url_flag, "--url flag"
    else:
        url, origin = service.url, (service.source_of("url") if service.url else "none")
    return TrainingTarget(source_name=source_name, url=url, url_origin=origin, service=service, persona=persona)


def print_training_target(target: TrainingTarget, console: Console) -> None:
    """Show what `train run` resolved: service file, persona, URL, and chrome crop (with provenance)."""
    service = target.service
    where = str(service.service_file) if service.service_file else "no service file"
    console.print(f"[dim]Service: {escape(service.name)} ({escape(where)})[/dim]")
    if target.persona is not None:
        console.print(f"[dim]Persona: {escape(target.persona.id)} (source {escape(target.source_name)})[/dim]")
    if target.url:
        console.print(f"[dim]Auto-navigate URL: {escape(target.url)} (from {escape(target.url_origin)})[/dim]")
    crop = service.config.chrome_height_px
    console.print(f"[dim]Chrome crop: {crop}px (from {escape(service.source_of('chrome_height_px'))})[/dim]")
