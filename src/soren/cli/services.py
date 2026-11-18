"""Services command group — inspect and validate config/services definitions (ADR-0002)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from soren.models.service import KeyboardShortcut, ServiceConfigError
from soren.services.config_loader import load_settings
from soren.services.persona_loader import get_personas_dir, load_all
from soren.services.service_deprecations import collect_notices
from soren.services.service_migration import apply_migration, plan_migration
from soren.services.service_prompt import format_shortcut, render_service_guidance
from soren.services.service_registry import ServiceRegistry
from soren.services.service_resolution import (
    ResolvedService,
    SourceSelectionError,
    resolve_service,
    select_task_sources,
)
from soren.services.service_writer import ServiceWriter

_EXAMPLE_HINT = "Copy config/services/acmepanel.yaml to config/services/<name>.yaml to add one."

_services_dir_option = click.option(
    "--services-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Service definitions directory (default: config/services)",
)


def _console() -> Console:
    # Created per command so Rich picks up the current terminal width (COLUMNS).
    return Console()


@click.group()
def services():
    """Inspect and validate service definitions (config/services/*.yaml)."""


@services.command("list")
@_services_dir_option
def services_list(services_dir: Path | None) -> None:
    """List service definition files and whether each is valid."""
    console = _console()
    registry = ServiceRegistry(services_dir)
    results = registry.validate_all()
    if not results:
        console.print(f"No service definitions found in {registry.services_dir}. {_EXAMPLE_HINT}")
        return

    table = Table(title="Services")
    table.add_column("Service", style="cyan")
    table.add_column("Type")
    table.add_column("URL", overflow="fold")
    table.add_column("File", style="dim")
    table.add_column("Status")
    for r in results:
        if r.service is not None:
            svc = r.service
            table.add_row(escape(svc.name), svc.type or "-", escape(svc.url or "-"), r.path.name, "[green]OK[/green]")
        else:
            table.add_row(escape(r.path.stem), "-", "-", r.path.name, "[red]INVALID[/red]")
    console.print(table)
    if any(not r.ok for r in results):
        console.print("[yellow]Run `soren services validate` for details.[/yellow]")


def _format_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, list):
        if not value:
            return "[]"
        return "\n".join(format_shortcut(v) if isinstance(v, KeyboardShortcut) else str(v) for v in value)
    if isinstance(value, str):
        return value.strip() or '""'
    return str(value)


def _values_table(resolved: ResolvedService) -> Table:
    table = Table(title=f"Resolved configuration: {escape(resolved.name)}")
    table.add_column("Field", style="cyan")
    table.add_column("Value", overflow="fold")
    table.add_column("Source", style="dim", overflow="fold")
    config = resolved.config.model_dump()
    for field_name, source in resolved.provenance.items():
        raw = getattr(resolved.config, field_name) if field_name in config else getattr(resolved, field_name)
        table.add_row(field_name, escape(_format_value(raw)), escape(source))
    return table


@services.command("show")
@click.argument("name")
@click.option("--persona", "persona_id", default=None, help="Resolve through this persona's task source")
@click.option("--prompt", "show_prompt", is_flag=True, help="Also print the guidance text the agent will receive")
@click.option("--config", type=click.Path(exists=True, path_type=Path), default=None, help="Path to settings.yaml")
@_services_dir_option
def services_show(
    name: str, persona_id: str | None, show_prompt: bool, config: Path | None, services_dir: Path | None
) -> None:
    """Show the effective configuration for service NAME and where each value comes from."""
    console = _console()
    registry = ServiceRegistry(services_dir)
    settings = load_settings(config)
    personas = load_all(get_personas_dir())

    try:
        matches = select_task_sources(personas, name, persona_id)
    except SourceSelectionError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise SystemExit(1)
    persona, source = matches[0] if matches else (None, None)
    service_name = source.service_key if source else name

    try:
        resolved = resolve_service(
            service_name,
            registry=registry,
            settings=settings,
            source=source,
            persona_id=persona.id if persona else None,
        )
    except ServiceConfigError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise SystemExit(1)

    if resolved.service_file is not None:
        console.print(f"Service file: {resolved.service_file}")
    else:
        console.print(
            f"[yellow]No service file at {registry.path_for(service_name)}; "
            "showing persona/settings/default values.[/yellow]"
        )
    if persona is not None and source is not None:
        console.print(f"Persona source: {escape(persona.id)} / {escape(source.name)}")
    console.print(_values_table(resolved))

    if show_prompt:
        guidance = render_service_guidance(resolved)
        console.print("\n[bold]Agent prompt preview:[/bold]")
        if guidance:
            console.print(escape(guidance))
        else:
            console.print("[dim](no service guidance: nothing would be added to the agent prompt)[/dim]")


@services.command("validate")
@click.option("--config", type=click.Path(exists=True, path_type=Path), default=None, help="Path to settings.yaml")
@_services_dir_option
def services_validate(config: Path | None, services_dir: Path | None) -> None:
    """Validate every service file and every persona `service:` reference. Exits 1 on any problem.

    Also lists deprecated or redundant legacy configuration (these never fail validation).
    """
    console = _console()
    registry = ServiceRegistry(services_dir)
    personas = load_all(get_personas_dir())
    problems = 0

    results = registry.validate_all()
    for r in results:
        if r.ok:
            console.print(f"[green]OK[/green]       {r.path.name}")
        else:
            problems += 1
            console.print(f"[red]INVALID[/red]  {r.path.name}: {escape(r.error or '')}")

    references = 0
    for persona in personas:
        for source in persona.task_sources:
            if not source.service:
                continue
            references += 1
            expected = registry.path_for(source.service)
            if not expected.is_file():
                problems += 1
                console.print(
                    f"[red]MISSING[/red]  persona {persona.id!r} source {source.name!r} references missing "
                    f"service {source.service!r} (expected {expected})"
                )

    for notice in collect_notices(load_settings(config), personas, registry, os.environ):
        label = "[yellow]DEPRECATED[/yellow]" if notice.level == "deprecated" else "[cyan]INFO[/cyan]      "
        console.print(f"{label} {escape(notice.message)}")

    if not results and not references:
        console.print(f"No service definitions found in {registry.services_dir}. {_EXAMPLE_HINT}")
    if problems:
        console.print(f"[red]{problems} problem(s) found.[/red]")
        raise SystemExit(1)


@services.command("migrate")
@click.option("--dry-run", is_flag=True, help="Show what would be created without writing anything")
@click.option("--persona", "persona_id", default=None, help="Only create services used by this persona")
@_services_dir_option
def services_migrate(dry_run: bool, persona_id: str | None, services_dir: Path | None) -> None:
    """Create config/services/<name>.yaml files from inline persona task_source values.

    Persona files are never modified and existing service files are never overwritten.
    Services whose personas disagree on a value are reported and skipped.
    """
    console = _console()
    registry = ServiceRegistry(services_dir)
    try:
        plan = plan_migration(load_all(get_personas_dir()), registry, persona_id)
    except ValueError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise SystemExit(1)

    if plan.is_empty:
        console.print("Nothing to migrate: no persona task source has inline service settings without a service file.")
        return

    verb = "Would create" if dry_run else "Created"
    if not dry_run:
        apply_migration(plan, ServiceWriter(registry))
    for planned in plan.creates:
        values = planned.service.model_dump(exclude_none=True, exclude={"version", "name"})
        fields = sorted([k for k in values if k != "config"] + list(values.get("config", {})))
        sources = ", ".join(str(s) for s in planned.sources)
        console.print(f"[green]{verb}[/green] {planned.path} ({', '.join(fields)}) from {escape(sources)}")
    for name, path in plan.existing:
        console.print(f"[dim]Already defined (skipped):[/dim] {escape(name)} ({path})")
    for conflict in plan.conflicts:
        values = "; ".join(f"{k}={v!r}" for k, v in conflict.values.items())
        console.print(f"[red]Conflict (not migrated):[/red] {escape(conflict.name)} {conflict.field}: {escape(values)}")
    for notice in plan.inherits:
        console.print(
            f"[yellow]Note:[/yellow] {escape(str(notice.source))} will now inherit "
            f"{notice.field}={notice.value!r} from {escape(notice.name)}'s service file"
        )
    if plan.creates:
        console.print(
            "[dim]Persona inline values still take precedence over the new files; remove them from the "
            "persona once you've checked `soren services show <name>`.[/dim]"
        )
