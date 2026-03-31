"""Playbooks command group."""

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table


@click.group()
def playbooks():
    """Manage service playbooks."""


@playbooks.command("list")
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to settings.yaml")
def playbooks_list(config):
    """List all service playbooks with metadata."""
    from soren.services.config_loader import load_settings
    from soren.services.llm_service import LLMService
    from soren.services.playbook_service import PlaybookService

    settings = load_settings(Path(config) if config else None)
    llm = LLMService(settings)
    svc = PlaybookService(settings, llm)
    infos = svc.list_playbooks()
    if not infos:
        click.echo("No playbooks found.")
        return
    table = Table(title="Service Playbooks")
    table.add_column("Service", style="cyan")
    table.add_column("Size", justify="right", style="dim")
    table.add_column("Last Modified", style="yellow")
    table.add_column("Sections", justify="right")
    for p in infos:
        table.add_row(
            p.service_name,
            f"{p.file_size} B",
            p.last_modified[:19] if len(p.last_modified) > 19 else p.last_modified,
            str(p.section_count),
        )
    console = Console()
    console.print(table)


@playbooks.command("show")
@click.argument("service")
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to settings.yaml")
def playbooks_show(service, config):
    """Show a specific service playbook."""
    from soren.services.config_loader import load_settings
    from soren.services.llm_service import LLMService
    from soren.services.playbook_service import PlaybookService

    settings = load_settings(Path(config) if config else None)
    llm = LLMService(settings)
    svc = PlaybookService(settings, llm)
    content = svc.load(service)
    if content is None:
        click.echo(f"No playbook found for service: {service}", err=True)
        raise SystemExit(1)
    console = Console()
    console.print(content)
