"""Discover command group."""

import asyncio
from pathlib import Path

import click

from soren.services.config_loader import load_settings


@click.group()
def discover():
    """Task discovery operations."""


@discover.command("refresh")
@click.option("--persona", default=None, help="Filter by persona ID")
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to settings.yaml")
def discover_refresh(persona, config):
    """Scan task sources and populate the backlog."""

    from soren.adapters import get_adapter
    from soren.services.database import DatabaseManager
    from soren.services.llm_service import LLMService
    from soren.services.lume_client import LumeConnectionError
    from soren.services.persona_loader import get_personas_dir, load_all
    from soren.services.task_backlog import TaskBacklog, get_default_db_path
    from soren.services.task_discovery import TaskDiscovery
    from soren.services.vm_manager import VMManager

    settings = load_settings(Path(config) if config else None)
    personas = load_all(get_personas_dir())
    if persona:
        personas = [p for p in personas if p.id == persona]
    if not personas:
        click.echo("No personas found or matched filter.")
        return

    async def _run():
        db = DatabaseManager(get_default_db_path())
        await db.initialize()
        backlog = TaskBacklog(
            db,
            retry_backoff_minutes=getattr(settings.scheduler, "retry_backoff_minutes", None),
        )
        llm = LLMService(settings)
        vm = VMManager(settings.vm)
        discovery = TaskDiscovery(settings, llm, vm, backlog)

        total_inserted = 0
        try:
            await vm.ensure_ready()
            computer = await vm.connect_computer()
        except LumeConnectionError as e:
            click.echo(str(e), err=True)
            raise SystemExit(1)

        for p in personas:
            for source in p.task_sources:
                if not source.enabled:
                    continue
                if not get_adapter(source.type):
                    continue
                click.echo(f"Discovering: {p.name} / {source.name}...")
                try:
                    tasks = await discovery.discover(p, source, computer)
                    if tasks:
                        result = await backlog.insert_discovered(tasks)
                        total_inserted += result.inserted
                        click.echo(f"  Inserted: {result.inserted}, skipped: {result.skipped}")
                    else:
                        click.echo("  No tasks found.")
                except Exception as e:
                    click.echo(f"  Error: {e}", err=True)

        await db.close()
        click.echo(f"Done. Total inserted: {total_inserted}")

    asyncio.run(_run())
