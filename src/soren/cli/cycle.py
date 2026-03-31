"""Cycle command group."""

import asyncio
from pathlib import Path

import click
from rich.console import Console


@click.group()
def cycle():
    """Manage execution cycles."""


@cycle.command("run")
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to settings.yaml")
@click.option("--dry-run", is_flag=True, help="Preview playbook write-back without writing to disk")
def cycle_run(config, dry_run):
    """Run a full Soren cycle: discover (if needed) -> execute one task -> finalize."""

    from soren.services.config_loader import load_settings
    from soren.services.database import DatabaseManager
    from soren.services.llm_service import LLMService
    from soren.services.meta_agent import SorenMetaAgent
    from soren.services.persona_loader import get_personas_dir, load_all
    from soren.services.playbook_service import PlaybookService
    from soren.services.task_backlog import TaskBacklog, get_default_db_path
    from soren.services.task_discovery import TaskDiscovery
    from soren.services.task_executor import TaskExecutor
    from soren.services.vm_manager import LumeConnectionError, VMManager

    settings = load_settings(Path(config) if config else None)

    async def _run():
        click.echo("Assessing backlog...")
        db = DatabaseManager(get_default_db_path())
        await db.initialize()
        backlog = TaskBacklog(
            db,
            retry_backoff_minutes=getattr(settings.scheduler, "retry_backoff_minutes", None),
        )
        llm = LLMService(settings)
        vm = VMManager(settings.vm)
        playbook_svc = PlaybookService(settings, llm)
        discovery = TaskDiscovery(settings, llm, vm, backlog)
        executor = TaskExecutor(settings, llm, vm, playbook_service=playbook_svc)

        def load_personas():
            return load_all(get_personas_dir())

        agent = SorenMetaAgent(
            settings=settings,
            db=db,
            llm=llm,
            vm=vm,
            backlog=backlog,
            discovery=discovery,
            executor=executor,
            persona_loader=load_personas,
            report_dir=Path("data/reports"),
            playbook_service=playbook_svc,
            dry_run=dry_run,
        )

        try:
            report = await agent.run_cycle()
            if report.task_executed_id:
                steps = (report.task_result and report.task_result.steps_taken) or 0
                click.echo(f"Executed task: {report.task_executed_id[:8]}... ({steps} steps)")
            if getattr(report, "playbook_preview", None):
                console = Console()
                console.print("\n[dim]Playbook preview (--dry-run):[/dim]")
                console.print(report.playbook_preview[:2000] + ("..." if len(report.playbook_preview) > 2000 else ""))
            click.echo("Cycle complete.")
        except LumeConnectionError as e:
            click.echo(str(e), err=True)
            raise SystemExit(1)
        finally:
            await db.close()

    asyncio.run(_run())
