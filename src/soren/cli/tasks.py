"""Tasks command group."""

import asyncio

import click
from rich.console import Console
from rich.table import Table


@click.group()
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
                    t.id[:8] + "\u2026" if len(t.id) > 8 else t.id,
                    (t.title[:40] + "\u2026") if len(t.title) > 40 else t.title,
                    t.status,
                    t.persona_id or "\u2014",
                    t.task_type or "\u2014",
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
