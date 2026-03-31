"""Personas command group."""

import click
from rich.console import Console
from rich.table import Table


@click.group()
def personas():
    """Manage agent personas."""


@personas.command("validate")
def personas_validate():
    """Validate all persona YAML files."""
    import yaml
    from pydantic import ValidationError

    from soren.services.persona_loader import get_personas_dir, load_persona

    personas_dir = get_personas_dir()
    files = sorted(personas_dir.glob("*.yaml"))
    if not files:
        click.echo(f"No persona YAML files found in {personas_dir}.")
        return

    table = Table(title="Persona Validation")
    table.add_column("File", style="cyan")
    table.add_column("Status", style="yellow")
    table.add_column("Details", style="dim")

    invalid_count = 0
    for file_path in files:
        try:
            persona = load_persona(file_path)
            source_count = len(persona.task_sources)
            table.add_row(file_path.name, "OK", f"id={persona.id}, sources={source_count}")
        except (ValidationError, yaml.YAMLError) as e:
            invalid_count += 1
            table.add_row(file_path.name, "INVALID", str(e).splitlines()[0][:140])
    console = Console()
    console.print(table)
    if invalid_count:
        raise SystemExit(1)


@personas.command("list")
def personas_list():
    """List all configured personas."""
    from soren.services.persona_loader import get_personas_dir, load_all

    personas = load_all(get_personas_dir())
    if not personas:
        click.echo("No personas found.")
        return
    table = Table(title="Configured Personas")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="yellow")
    table.add_column("Enabled", style="green")
    table.add_column("Task Sources", justify="right")
    for persona in personas:
        table.add_row(
            persona.id,
            persona.name,
            "yes" if persona.enabled else "no",
            str(len(persona.task_sources)),
        )
    console = Console()
    console.print(table)
