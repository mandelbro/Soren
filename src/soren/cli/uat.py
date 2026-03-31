"""UAT command group."""

import click


@click.group()
def uat():
    """User-acceptance testing helpers."""


@uat.command("checklist")
def uat_checklist():
    """Show the ready-for-user-testing smoke checklist."""
    checklist = [
        "1) Run a full cycle",
        "   - soren cycle run",
        "2) Confirm cycle reporting output",
        "   - soren history show --limit 5",
        "3) Confirm encrypted credentials are available",
        "   - soren credentials list",
        "4) Reset browser state and verify clean baseline",
        "   - soren vm reset",
    ]
    click.echo("Soren UAT Smoke Checklist")
    click.echo("-------------------------")
    click.echo("\n".join(checklist))
    click.echo("\nReference: docs/uat-smoke-checklist.md")
