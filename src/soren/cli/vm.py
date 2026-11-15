"""VM command group."""

import asyncio
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from soren.services.config_loader import load_settings
from soren.services.lume_client import LumeConnectionError
from soren.services.vm_manager import VMManager


@click.group()
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
                name = vm.get("name", "\u2014")
                status = vm.get("status", "\u2014")
                memory = vm.get("memory", "\u2014")
                vnc = vm.get("vncUrl", "\u2014")
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
