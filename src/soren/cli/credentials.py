"""Credentials command group."""

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table


@click.group()
def credentials():
    """Manage encrypted service credentials."""


@credentials.command("encrypt")
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True),
    default="config/credentials.yaml",
    show_default=True,
    help="Path to plaintext YAML/JSON credential file",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(),
    default="config/credentials.enc",
    show_default=True,
    help="Path for encrypted credential payload",
)
@click.option(
    "--key-file",
    type=click.Path(),
    default="data/credentials.key",
    show_default=True,
    help="Fernet key path (ignored when SOREN_CREDENTIALS_KEY is set)",
)
def credentials_encrypt(input_path, output_path, key_file):
    """Encrypt a plaintext credentials file for at-rest storage."""
    from soren.services.credentials_service import CredentialStore

    store = CredentialStore(Path(output_path), key_path=Path(key_file))
    try:
        count = store.encrypt_plaintext_file(Path(input_path))
        click.echo(f"Encrypted {count} credential record(s) -> {output_path}")
    except (ValueError, FileNotFoundError) as e:
        click.echo(str(e), err=True)
        raise SystemExit(1)


@credentials.command("add")
@click.argument("ref")
@click.argument("service_type")
@click.option("--username", prompt=True, help="Service username/email")
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True, help="Service password")
@click.option(
    "--output",
    "output_path",
    type=click.Path(),
    default="config/credentials.enc",
    show_default=True,
    help="Path for encrypted credential payload",
)
@click.option(
    "--key-file",
    type=click.Path(),
    default="data/credentials.key",
    show_default=True,
    help="Fernet key path (ignored when SOREN_CREDENTIALS_KEY is set)",
)
def credentials_add(ref, service_type, username, password, output_path, key_file):
    """Add or update a credential reference in encrypted storage."""
    from soren.services.credentials_service import CredentialStore

    store = CredentialStore(Path(output_path), key_path=Path(key_file))
    try:
        store.add(
            ref=ref,
            service_type=service_type,
            username=username,
            password=password,
        )
        click.echo(f"Stored credential ref '{ref}' for service '{service_type}'.")
    except ValueError as e:
        click.echo(str(e), err=True)
        raise SystemExit(1)


@credentials.command("list")
@click.option(
    "--input",
    "input_path",
    type=click.Path(),
    default="config/credentials.enc",
    show_default=True,
    help="Path to encrypted credential payload",
)
@click.option(
    "--key-file",
    type=click.Path(),
    default="data/credentials.key",
    show_default=True,
    help="Fernet key path (ignored when SOREN_CREDENTIALS_KEY is set)",
)
def credentials_list(input_path, key_file):
    """List credential references (no secrets shown)."""
    from soren.services.credentials_service import CredentialStore

    store = CredentialStore(Path(input_path), key_path=Path(key_file))
    try:
        refs = store.list_refs()
    except ValueError as e:
        click.echo(str(e), err=True)
        raise SystemExit(1)
    if not refs:
        click.echo("No encrypted credentials found.")
        return
    table = Table(title="Credential References")
    table.add_column("Ref", style="cyan")
    table.add_column("Service", style="yellow")
    table.add_column("Updated", style="dim")
    for entry in refs:
        table.add_row(entry.ref, entry.service_type, entry.updated_at)
    console = Console()
    console.print(table)
