"""Click CLI for the Soren Desktop Agent Framework."""

from dotenv import load_dotenv

load_dotenv()

import asyncio
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from soren.services.config_loader import load_settings
from soren.services.vm_manager import LumeConnectionError, VMManager


@click.group()
@click.version_option(version="0.1.0", prog_name="soren")
def cli():
    """Soren Desktop Agent Framework — Vision-based autonomous task execution."""


# --- Cycle group ---


@cli.group()
def cycle():
    """Manage execution cycles."""


@cycle.command("run")
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to settings.yaml")
def cycle_run(config):
    """Run a full Soren cycle: discover (if needed) -> execute one task -> finalize."""
    from pathlib import Path

    from soren.services.config_loader import load_settings
    from soren.services.database import DatabaseManager
    from soren.services.llm_service import LLMService
    from soren.services.meta_agent import SorenMetaAgent
    from soren.services.persona_loader import get_personas_dir, load_all
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
        discovery = TaskDiscovery(settings, llm, vm, backlog)
        executor = TaskExecutor(settings, llm, vm)

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
        )

        try:
            report = await agent.run_cycle()
            if report.task_executed_id:
                steps = (report.task_result and report.task_result.steps_taken) or 0
                click.echo(f"Executed task: {report.task_executed_id[:8]}... ({steps} steps)")
            click.echo("Cycle complete.")
        except LumeConnectionError as e:
            click.echo(str(e), err=True)
            raise SystemExit(1)
        finally:
            await db.close()

    asyncio.run(_run())


# --- Discover group ---


@cli.group()
def discover():
    """Task discovery operations."""


@discover.command("refresh")
@click.option("--persona", default=None, help="Filter by persona ID")
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to settings.yaml")
def discover_refresh(persona, config):
    """Scan task sources and populate the backlog."""
    from pathlib import Path

    from soren.adapters import get_adapter
    from soren.services.database import DatabaseManager
    from soren.services.llm_service import LLMService
    from soren.services.persona_loader import get_personas_dir, load_all
    from soren.services.task_backlog import TaskBacklog, get_default_db_path
    from soren.services.task_discovery import TaskDiscovery
    from soren.services.vm_manager import LumeConnectionError, VMManager

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


# --- Tasks group ---


@cli.group()
def tasks():
    """Manage the task backlog."""


@tasks.command("list")
@click.option("--persona", default=None, help="Filter by persona ID")
@click.option("--status", default=None, help="Filter by status")
@click.option("--type", "task_type", default=None, help="Filter by task type")
@click.option("--limit", default=None, type=int, help="Limit results")
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to settings.yaml")
def tasks_list(persona, status, task_type, limit, config):
    """List tasks in the backlog."""
    from soren.services.database import DatabaseManager
    from soren.services.task_backlog import TaskBacklog, get_default_db_path

    async def _run():
        db_path = get_default_db_path()
        db = DatabaseManager(db_path)
        await db.initialize()
        try:
            backlog = TaskBacklog(db)
            tasks = await backlog.list_tasks(
                persona_id=persona,
                status=status,
                task_type=task_type,
                limit=limit,
            )
            table = Table(title="Task Backlog")
            table.add_column("ID", style="dim")
            table.add_column("Title", style="cyan")
            table.add_column("Status", style="green")
            table.add_column("Persona", style="yellow")
            table.add_column("Type", style="blue")
            table.add_column("Priority", justify="right")
            table.add_column("Attempts", justify="right")
            for t in tasks:
                table.add_row(
                    t.id[:8] + "…" if len(t.id) > 8 else t.id,
                    (t.title[:40] + "…") if len(t.title) > 40 else t.title,
                    t.status,
                    t.persona_id or "—",
                    t.task_type or "—",
                    str(t.priority),
                    str(t.attempt_count),
                )
            console = Console()
            if tasks:
                console.print(table)
            else:
                console.print("No tasks in backlog.")
        finally:
            await db.close()

    asyncio.run(_run())


@tasks.command("retry-failed")
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to settings.yaml")
def tasks_retry_failed(config):
    """Requeue all failed tasks."""
    from soren.services.database import DatabaseManager
    from soren.services.task_backlog import TaskBacklog, get_default_db_path

    async def _run():
        db_path = get_default_db_path()
        db = DatabaseManager(db_path)
        await db.initialize()
        try:
            backlog = TaskBacklog(db)
            n = await backlog.requeue_all_dead_letter()
            click.echo(f"Requeued {n} dead-letter task(s).")
        finally:
            await db.close()

    asyncio.run(_run())


@tasks.command("retry")
@click.argument("task_id")
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to settings.yaml")
def tasks_retry(task_id, config):
    """Requeue a specific dead-letter task."""
    from soren.services.database import DatabaseManager
    from soren.services.task_backlog import TaskBacklog, get_default_db_path

    async def _run():
        db_path = get_default_db_path()
        db = DatabaseManager(db_path)
        await db.initialize()
        try:
            backlog = TaskBacklog(db)
            await backlog.requeue_task(task_id)
            click.echo(f"Requeued task {task_id}.")
        finally:
            await db.close()

    asyncio.run(_run())


@tasks.command("clear")
@click.confirmation_option(prompt="Are you sure you want to clear the backlog?")
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to settings.yaml")
def tasks_clear(config):
    """Clear all tasks from the backlog."""
    from soren.services.database import DatabaseManager
    from soren.services.task_backlog import TaskBacklog, get_default_db_path

    async def _run():
        db_path = get_default_db_path()
        db = DatabaseManager(db_path)
        await db.initialize()
        try:
            backlog = TaskBacklog(db)
            n = await backlog.clear_all()
            click.echo(f"Cleared {n} task(s) from backlog.")
        finally:
            await db.close()

    asyncio.run(_run())


# --- VM group ---


@cli.group()
def vm():
    """Manage the Lume macOS VM."""


@vm.command("prepare")
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to settings.yaml")
def vm_prepare(config):
    """Create and configure the pristine VM image (one-time setup)."""
    from soren.services.vm_prepare import prepare_pristine_vm

    async def _run():
        try:
            await prepare_pristine_vm(config_path=Path(config) if config else None)
            click.echo("Pristine VM prepared successfully.")
        except LumeConnectionError as e:
            click.echo(str(e), err=True)
            raise SystemExit(1)
        except RuntimeError as e:
            click.echo(str(e), err=True)
            raise SystemExit(1)

    asyncio.run(_run())


@vm.command("status")
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to settings.yaml")
def vm_status(config):
    """Show VM status via Lume HTTP API."""
    settings = load_settings(Path(config) if config else None)
    manager = VMManager(settings.vm)

    async def _run():
        try:
            vms = await manager.list_vms()
            table = Table(title="Lume VMs")
            table.add_column("Name", style="cyan")
            table.add_column("Status", style="green")
            table.add_column("Memory", style="yellow")
            table.add_column("VNC URL", style="dim")
            for vm in vms:
                name = vm.get("name", "—")
                status = vm.get("status", "—")
                memory = vm.get("memory", "—")
                vnc = vm.get("vncUrl", "—")
                table.add_row(str(name), str(status), str(memory), str(vnc))
            console = Console()
            console.print(table)
        except LumeConnectionError as e:
            click.echo(str(e), err=True)
            raise SystemExit(1)

    asyncio.run(_run())


@vm.command("stop")
@click.confirmation_option(prompt="Stop the running VM?")
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to settings.yaml")
def vm_stop(config):
    """Stop the running VM."""
    settings = load_settings(Path(config) if config else None)
    manager = VMManager(settings.vm)

    async def _run():
        try:
            if not await manager.is_running():
                click.echo("VM is not running.")
                return
            await manager.stop()
            click.echo("VM stopped.")
        except LumeConnectionError as e:
            click.echo(str(e), err=True)
            raise SystemExit(1)

    asyncio.run(_run())


@vm.command("reset")
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to settings.yaml")
def vm_reset(config):
    """Reset browser state: close all Safari windows, open a fresh one."""
    settings = load_settings(Path(config) if config else None)
    manager = VMManager(settings.vm)

    async def _run():
        try:
            result = await manager.reset_browser_state()
            status = "[green]OK[/green]" if result.success else "[red]FAILED[/red]"
            console = Console()
            console.print(f"Browser reset: {status} ({result.duration_seconds:.1f}s)")
            console.print(f"  Windows after reset: {result.window_count}")
            if result.warnings:
                for w in result.warnings:
                    console.print(f"  [yellow]Warning:[/yellow] {w}")
            if not result.success:
                raise SystemExit(1)
        except LumeConnectionError as e:
            click.echo(str(e), err=True)
            raise SystemExit(1)

    asyncio.run(_run())


# --- Credentials group ---


@cli.group()
def credentials():
    """Manage encrypted service credentials."""


@credentials.command("encrypt")
def credentials_encrypt():
    """Encrypt a plaintext credentials file."""
    click.echo("Not yet implemented: credentials encrypt")


@credentials.command("add")
@click.argument("ref")
@click.argument("service_type")
def credentials_add(ref, service_type):
    """Add a credential for a service."""
    click.echo("Not yet implemented: credentials add")


@credentials.command("list")
def credentials_list():
    """List credential references (no secrets shown)."""
    click.echo("Not yet implemented: credentials list")


# --- Playbooks group ---


@cli.group()
def playbooks():
    """Manage service playbooks."""


@playbooks.command("list")
def playbooks_list():
    """List all service playbooks."""
    click.echo("Not yet implemented: playbooks list")


@playbooks.command("show")
@click.argument("service")
def playbooks_show(service):
    """Show a specific service playbook."""
    click.echo("Not yet implemented: playbooks show")


# --- Personas group ---


@cli.group()
def personas():
    """Manage agent personas."""


@personas.command("validate")
def personas_validate():
    """Validate all persona YAML files."""
    click.echo("Not yet implemented: personas validate")


@personas.command("list")
def personas_list():
    """List all configured personas."""
    click.echo("Not yet implemented: personas list")


# --- Resources group ---


@cli.group()
def resources():
    """Resource validation and memory checks."""


@resources.command("check")
def resources_check():
    """Validate memory and Ollama config for 32 GB machines. Run before begin-soren-cycle."""
    import os

    import psutil

    console = Console()
    issues: list[str] = []
    warnings: list[str] = []

    # Memory
    vm = psutil.virtual_memory()
    total_gb = vm.total / (1024**3)
    used_pct = vm.percent
    if total_gb < 32:
        warnings.append(f"Machine has {total_gb:.1f} GB RAM; 32 GB recommended for full cycle.")
    if used_pct >= 85:
        issues.append(f"Memory at {used_pct:.1f}% — close apps before running cycle.")

    # Ollama env (critical for 32 GB) — must be set when starting the Soren Ollama instance
    max_models = os.environ.get("OLLAMA_MAX_LOADED_MODELS")
    if max_models != "1":
        issues.append(
            "OLLAMA_MAX_LOADED_MODELS should be 1 (prevents OOM). "
            "Set in .env and run: source .env && ollama serve (or configure launchd for port 11435)"
        )
    keep_alive = os.environ.get("OLLAMA_KEEP_ALIVE")
    if keep_alive and keep_alive != "0":
        warnings.append(
            f"OLLAMA_KEEP_ALIVE={keep_alive}; 0 recommended for immediate model unload."
        )

    if issues:
        console.print("[red]Issues:[/red]")
        for m in issues:
            console.print(f"  • {m}")
    if warnings:
        console.print("[yellow]Warnings:[/yellow]")
        for m in warnings:
            console.print(f"  • {m}")
    if not issues and not warnings:
        console.print("[green]OK[/green] — Memory and Ollama config look good.")
    elif not issues:
        console.print("[green]OK[/green] — No blocking issues.")
    else:
        raise SystemExit(1)


# --- History group ---


@cli.group()
def history():
    """View execution history."""


@history.command("show")
@click.option("--limit", default=10, type=int, help="Number of entries")
def history_show(limit):
    """Show recent cycle history."""
    click.echo("Not yet implemented: history show")
