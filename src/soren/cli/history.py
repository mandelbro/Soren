"""History command group."""

import json
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table


@click.group()
def history():
    """View execution history."""


@history.command("show")
@click.option("--limit", default=10, type=int, help="Number of entries")
@click.option(
    "--report-dir",
    type=click.Path(),
    default="data/reports",
    show_default=True,
    help="Directory containing cycle report JSON files",
)
def history_show(limit, report_dir):
    """Show recent cycle history."""
    report_path = Path(report_dir)
    if not report_path.exists():
        click.echo(f"No report directory found at {report_path}.")
        return

    entries: list[dict] = []
    for path in sorted(report_path.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.name == "latest.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entries.append(data)
        except json.JSONDecodeError:
            continue
        if len(entries) >= limit:
            break

    if not entries:
        click.echo("No cycle reports found.")
        return

    table = Table(title="Recent Cycles")
    table.add_column("Cycle", style="cyan")
    table.add_column("Started", style="dim")
    table.add_column("Type", style="yellow")
    table.add_column("Task", style="blue")
    table.add_column("Status", style="green")
    table.add_column("Duration", justify="right")

    for item in entries:
        cycle_id = str(item.get("cycle_id", ""))[:8]
        started = str(item.get("started_at", ""))[:19]
        cycle_type = str(item.get("cycle_type", "execute"))
        task_id = str(item.get("task_executed_id") or "\u2014")
        task_result = item.get("task_result") or {}
        status = "n/a"
        if isinstance(task_result, dict) and "success" in task_result:
            status = "success" if task_result.get("success") else "failed"
        duration = f"{float(item.get('total_duration_seconds', 0.0)):.1f}s"
        table.add_row(cycle_id or "\u2014", started or "\u2014", cycle_type, task_id[:8], status, duration)

    console = Console()
    console.print(table)
