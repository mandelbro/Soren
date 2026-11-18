"""Train command group — interactive training and playbook management.

Provides ``soren train run <source>`` for interactive training sessions. The
``soren train list/show/delete`` playbook commands live in
``soren.cli.train_playbooks`` and are registered on this group."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import click
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from soren.cli.train_playbooks import PLAYBOOK_COMMANDS
from soren.cli.training_callbacks import RichTrainingCallbacks
from soren.cli.training_target import print_training_target, resolve_training_target
from soren.models.service import ServiceConfigError
from soren.services.config_loader import load_settings
from soren.services.llm_service import LLMService
from soren.services.persona_loader import get_personas_dir, load_all
from soren.services.playbook_service import PlaybookService
from soren.services.service_registry import ServiceRegistry
from soren.services.training_session import TrainingSession
from soren.services.vm_manager import VMManager

console = Console()


# Loggers whose INFO/DEBUG output is most useful when diagnosing a training run.
# Bumped to INFO unconditionally and DEBUG when --debug is passed; the rest of
# the application keeps Python's default WARNING threshold so noise stays low.
_TRAINING_LOGGERS = (
    "soren.services.training_session",
    "soren.services.training_navigation",
    "soren.services.training_step_runner",
    "soren.services.training_verification",
    "soren.patches.vnc_screenshot",
    "soren.verification.verifier",
)


# Third-party loggers that emit WARNINGs on every ComputerAgent construction
# for optional integrations Soren does not use. Silence them so the actually-
# useful output above is not drowned in "X not installed" noise.
_SILENCED_NOISY_LOGGERS = (
    "core.telemetry.sentry",
    "core.telemetry.otel",
)


_TRAINING_HANDLER_MARKER = "_soren_training_handler"


def _configure_training_logging(debug: bool) -> None:
    """Surface Soren's training-relevant loggers above the default WARNING level.

    Without this, the click coordinates, VNC scale factor, and CUA action stream
    are emitted at INFO/DEBUG and never reach the operator. We attach a single
    Rich-friendly StreamHandler so the output interleaves cleanly with the Rich
    panels printed by ``RichTrainingCallbacks``.

    Also silences ``core.telemetry.sentry`` / ``core.telemetry.otel`` loggers
    (raised to ERROR) so their WARNING-level "not installed" messages stop
    drowning the training output on every ComputerAgent construction.
    """
    level = logging.DEBUG if debug else logging.INFO
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in _TRAINING_LOGGERS:
        lg = logging.getLogger(name)
        lg.setLevel(level)
        lg.propagate = False
        # Idempotent: skip if we've already attached a handler in this process.
        if any(getattr(h, _TRAINING_HANDLER_MARKER, False) for h in lg.handlers):
            continue
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        handler.setLevel(level)
        setattr(handler, _TRAINING_HANDLER_MARKER, True)
        lg.addHandler(handler)

    for name in _SILENCED_NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.ERROR)


_ALL_FLOWS = ("login", "discovery", "execution")


@click.group()
def train():
    """Training and playbook management commands."""


@train.command("run")
@click.argument("source")
@click.option("--flow", type=click.Choice(["login", "discovery", "execution"]), default=None, help="Flow type to train")
@click.option("--task-type", default=None, help="Task type (for execution flows)")
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to settings.yaml")
@click.option(
    "--url",
    default=None,
    help=(
        "Source URL to auto-navigate to before training begins. "
        "When provided, Soren dispatches Cmd+L + type URL + Return via the VM's keyboard, "
        "bypassing UI-TARS for the navigation step (see ADR-0001). "
        "Saves a training cycle on a deterministic step and works with the Safari chrome crop feature."
    ),
)
@click.option(
    "--debug",
    is_flag=True,
    default=False,
    help="Emit DEBUG-level logs (otherwise INFO) for soren.* loggers — useful for diagnosing click misses.",
)
@click.option(
    "--persona",
    "persona_id",
    default=None,
    help="Use this persona's task source (and its overrides) when several personas use SOURCE.",
)
def train_run(
    source: str,
    flow: str | None,
    task_type: str | None,
    config: str | None,
    url: str | None,
    debug: bool,
    persona_id: str | None,
) -> None:
    """Start an interactive training session for SOURCE (a task source or service name)."""
    _configure_training_logging(debug=debug)

    # Resolve the service config before touching the VM: an invalid config
    # must fail fast (ADR-0002), and the resolved chrome height replaces the
    # old manual SOREN_CHROME_CROP_Y step.
    settings = load_settings(Path(config) if config else None)
    try:
        target = resolve_training_target(
            source,
            url_flag=url,
            personas=load_all(get_personas_dir()),
            persona_id=persona_id,
            settings=settings,
            registry=ServiceRegistry(),
        )
    except ServiceConfigError as exc:
        console.print(f"[red]Service configuration error: {escape(str(exc))}[/red]")
        raise SystemExit(1)
    print_training_target(target, console)

    async def _run() -> None:
        llm = LLMService(settings)
        vm = VMManager(settings.vm)
        ps = PlaybookService(settings, llm)
        callbacks = RichTrainingCallbacks()

        flows_to_train = [flow] if flow else list(_ALL_FLOWS)

        for ft in flows_to_train:
            console.print(
                Panel(
                    f"Source: [bold]{target.source_name}[/]\nFlow: [bold]{ft}[/]\nCommands: /help /done /abort /undo /steps",
                    title=f"Training: {ft}",
                    border_style="cyan",
                )
            )

            session = TrainingSession(
                vm_manager=vm,
                playbook_service=ps,
                llm=llm,
                callbacks=callbacks,
                source_name=target.source_name,
                flow_type=ft,
                source_url=target.url,
                task_type=task_type if ft == "execution" else None,
                verification_model=settings.verification.model,
                service=target.service,
            )

            result = await session.run()

            if result is None:
                console.print("[yellow]Training aborted or cancelled.[/yellow]")
                return

            # Summary table
            steps = session.steps
            if steps:
                tbl = Table(title=f"Recorded Steps ({ft})")
                tbl.add_column("#", justify="right", style="dim")
                tbl.add_column("Instruction")
                tbl.add_column("Expected After", style="green")
                for s in steps:
                    tbl.add_row(str(s.seq), s.instruction, s.expected_after)
                console.print(tbl)

            console.print(f"[green]Playbook saved for {target.source_name} ({ft}).[/green]")

    asyncio.run(_run())


for _command in PLAYBOOK_COMMANDS:
    train.add_command(_command)
