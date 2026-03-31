"""Click CLI for the Soren Desktop Agent Framework."""

import click
from dotenv import load_dotenv

load_dotenv()


@click.group()
@click.version_option(version="0.1.0", prog_name="soren")
def cli():
    """Soren Desktop Agent Framework — Vision-based autonomous task execution."""


def _register_commands() -> None:
    """Register all command groups."""
    from soren.cli.credentials import credentials
    from soren.cli.cycle import cycle
    from soren.cli.discover import discover
    from soren.cli.history import history
    from soren.cli.personas import personas
    from soren.cli.playbooks import playbooks
    from soren.cli.resources import resources
    from soren.cli.tasks import tasks
    from soren.cli.uat import uat
    from soren.cli.vm import vm

    cli.add_command(credentials)
    cli.add_command(cycle)
    cli.add_command(discover)
    cli.add_command(history)
    cli.add_command(personas)
    cli.add_command(playbooks)
    cli.add_command(resources)
    cli.add_command(tasks)
    cli.add_command(uat)
    cli.add_command(vm)


_register_commands()
