"""Cycle command group."""

import asyncio
from pathlib import Path
from typing import Any, Literal

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from soren.services.meta_agent import InterventionCallbacks


class CLIInterventionCallbacks(InterventionCallbacks):
    """Rich-based interactive intervention prompt for attended mode."""

    _CHOICE_MAP: dict[str, Literal["train", "skip", "abort"]] = {"t": "train", "s": "skip", "a": "abort"}

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()

    async def on_stuck(
        self,
        source_name: str,
        stuck_reason: str,
        cycle_context: dict[str, Any],
    ) -> Literal["train", "skip", "abort"]:
        self._console.print(
            Panel(
                f"[bold red]Source stuck:[/bold red] {source_name}\n"
                f"[dim]Reason:[/dim] {stuck_reason}\n"
                f"[dim]Steps taken:[/dim] {cycle_context.get('steps_taken', '?')}",
                title="Stuck Detected",
                border_style="red",
            )
        )
        choice = Prompt.ask(
            "[bold][t][/] Train  [bold][s][/] Skip  [bold][a][/] Abort",
            choices=["t", "s", "a"],
            default="s",
            console=self._console,
        )
        mapped = self._CHOICE_MAP.get(choice)
        if mapped is not None:
            return mapped
        return "skip"


@click.group()
def cycle():
    """Manage execution cycles."""


@cycle.command("run")
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to settings.yaml")
@click.option("--dry-run", is_flag=True, help="Preview playbook write-back without writing to disk")
@click.option("--unattended", is_flag=True, help="Run without interactive prompts (auto-skip stuck sources)")
def cycle_run(config, dry_run, unattended):
    """Run a full Soren cycle: discover (if needed) -> execute one task -> finalize."""

    from soren.cli.training_callbacks import RichTrainingCallbacks
    from soren.services.config_loader import load_settings
    from soren.services.database import DatabaseManager
    from soren.services.llm_service import LLMService
    from soren.services.lume_client import LumeError
    from soren.services.meta_agent import SorenMetaAgent
    from soren.services.persona_loader import get_personas_dir, load_all
    from soren.services.playbook_service import PlaybookService
    from soren.services.task_backlog import TaskBacklog, get_default_db_path
    from soren.services.task_discovery import TaskDiscovery
    from soren.services.task_executor import TaskExecutor
    from soren.services.vm_manager import VMManager

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

        # Set up intervention callbacks for attended mode
        intervention_cbs = None if unattended else CLIInterventionCallbacks()
        training_cbs = None if unattended else RichTrainingCallbacks()

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
            intervention_callbacks=intervention_cbs,
            training_callbacks=training_cbs,
        )

        try:
            report = await agent.run_cycle()
            if report.task_executed_id:
                steps = (report.task_result and report.task_result.steps_taken) or 0
                click.echo(f"Executed task: {report.task_executed_id[:8]}... ({steps} steps)")
            preview: str | None = getattr(report, "playbook_preview", None)
            if preview:
                console = Console()
                console.print("\n[dim]Playbook preview (--dry-run):[/dim]")
                console.print(preview[:2000] + ("..." if len(preview) > 2000 else ""))
            if report.stuck_sources:
                console = Console()
                for src in report.stuck_sources:
                    mode = "auto-skipped" if src.get("auto_skipped") else "skipped"
                    console.print(
                        f"[yellow]Source {mode}: {src.get('source_name')} ({src.get('stuck_reason')})[/yellow]"
                    )
            click.echo("Cycle complete.")
        except LumeError as e:
            click.echo(str(e), err=True)
            raise SystemExit(1)
        finally:
            await db.close()

    asyncio.run(_run())
