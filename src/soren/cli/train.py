"""Train command group — interactive training and playbook management.

Provides ``soren train run <source>`` for interactive training sessions and
``soren train list/show/delete`` for playbook management.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from soren.models.persona import Persona
from soren.services.config_loader import load_settings
from soren.services.llm_service import LLMService
from soren.services.persona_loader import get_personas_dir, load_all
from soren.services.playbook_service import PlaybookService
from soren.services.training_session import TrainingCallbacks, TrainingSession
from soren.services.vm_manager import VMManager

console = Console()


# Loggers whose INFO/DEBUG output is most useful when diagnosing a training run.
# Bumped to INFO unconditionally and DEBUG when --debug is passed; the rest of
# the application keeps Python's default WARNING threshold so noise stays low.
_TRAINING_LOGGERS = (
    "soren.services.training_session",
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


def _resolve_source_url(
    source_name: str,
    url_flag: str | None,
    personas: list[Persona],
) -> str:
    """Resolve the task source URL for training-mode auto-navigation.

    Precedence:
      1. ``url_flag`` (explicit ``--url`` CLI argument) wins if provided.
      2. Otherwise scan ``personas`` in load order for a task_source whose
         ``name`` matches ``source_name`` and whose ``url`` is non-empty.
         The first match wins.
      3. If neither is available, return "" so TrainingSession skips
         auto-navigation and falls back to human-instructed navigation.

    This lets the operator run ``soren train run sproutful`` and have the
    URL automatically picked up from ``config/personas/*.yaml``'s
    ``task_sources`` list — the URL is already configured there for
    discovery, no reason to require re-entry at training time.
    """
    if url_flag:
        return url_flag
    for persona in personas:
        for source in persona.task_sources:
            if source.name == source_name and source.url:
                return source.url
    return ""


# Staleness thresholds per discovery document
_LOGIN_STALE_DAYS = 30
_DISCOVERY_STALE_DAYS = 30
_EXECUTION_STALE_DAYS = 14

_ALL_FLOWS = ("login", "discovery", "execution")


# --- Rich Training Callbacks ---


class RichTrainingCallbacks(TrainingCallbacks):
    """Rich terminal UI callbacks for interactive training."""

    async def show_screenshot(self, screenshot: bytes, vlm_description: str) -> None:
        console.print(
            Panel(vlm_description or "(No description available)", title="Current Screen", border_style="blue")
        )

    async def get_instruction(self) -> str:
        return console.input("[bold cyan]Instruction[/] (/help for commands): ")

    async def confirm_step(self, screenshot: bytes, vlm_description: str) -> str:
        console.print(Panel(vlm_description or "(No description)", title="Step Result", border_style="green"))
        return console.input("[bold]\\[y][/] Accept  [bold]\\[n][/] Redo  [bold]\\[d][/] Done: ")

    async def get_expected_after(self, vlm_suggestion: str) -> str:
        console.print(f"[dim]Suggested:[/] {vlm_suggestion}")
        user = console.input("Expected after (Enter to accept suggestion): ")
        return user if user.strip() else vlm_suggestion

    async def show_message(self, message: str) -> None:
        console.print(message)

    async def show_error(self, error: str) -> None:
        console.print(f"[red]{error}[/red]")

    # --- Autonomous verify+retry callbacks ---

    async def get_instruction_with_expected(self) -> tuple[str, str]:
        instruction = console.input("[bold cyan]Instruction[/] (/help for commands): ")
        if instruction.strip().startswith("/"):
            return instruction, ""
        expected = console.input("[bold cyan]Expected outcome[/]: ")
        return instruction, expected

    async def show_verification_result(
        self,
        attempt: int,
        max_attempts: int,
        passed: bool,
        confidence: float,
        reasoning: str | None,
    ) -> None:
        status = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
        content = f"Attempt: {attempt}/{max_attempts}\nResult: {status}\nConfidence: {confidence:.0%}"
        if reasoning:
            content += f"\nReasoning: {reasoning}"
        border = "green" if passed else "red"
        console.print(Panel(content, title="Verification", border_style=border))

    async def handle_stuck(
        self,
        instruction: str,
        expected_after: str,
        attempts: int,
        last_reasoning: str | None,
    ) -> str:
        console.print(
            Panel(
                f"Instruction: {instruction}\n"
                f"Expected: {expected_after}\n"
                f"Attempts: {attempts}\n"
                f"Last reasoning: {last_reasoning or 'N/A'}",
                title="[red]Stuck — Verification Failed[/red]",
                border_style="red",
            )
        )
        console.print(
            "  [bold]\\[r][/] Retry with new instruction\n"
            "  [bold]\\[s][/] Skip this step\n"
            "  [bold]\\[a][/] Accept as-is (unverified)\n"
            "  [bold]\\[q][/] Abort training"
        )
        choice = console.input("[bold]Choice[/]: ").strip().lower()
        if choice in ("r", "retry"):
            new_instr = console.input("[bold cyan]New instruction[/]: ").strip()
            return f"retry:{new_instr}"
        if choice in ("a", "accept"):
            return "accept"
        if choice in ("q", "abort"):
            return "abort"
        return "skip"  # Default: skip (includes "s" and unrecognized)


# --- Command Group ---


@click.group()
def train():
    """Training and playbook management commands."""


# --- train run ---


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
def train_run(
    source: str,
    flow: str | None,
    task_type: str | None,
    config: str | None,
    url: str | None,
    debug: bool,
) -> None:
    """Start an interactive training session for SOURCE."""
    _configure_training_logging(debug=debug)

    async def _run() -> None:
        settings = load_settings(Path(config) if config else None)
        llm = LLMService(settings)
        vm = VMManager(settings.vm)
        ps = PlaybookService(settings, llm)
        callbacks = RichTrainingCallbacks()

        # Resolve the URL for auto-navigation: explicit --url wins, else
        # look up the source by name in the persona task_sources list.
        personas = load_all(get_personas_dir())
        resolved_url = _resolve_source_url(source_name=source, url_flag=url, personas=personas)
        if resolved_url:
            origin = "--url flag" if url else "persona task_source"
            console.print(f"[dim]Auto-navigate URL: {resolved_url} (from {origin})[/dim]")

        flows_to_train = [flow] if flow else list(_ALL_FLOWS)

        for ft in flows_to_train:
            console.print(
                Panel(
                    f"Source: [bold]{source}[/]\nFlow: [bold]{ft}[/]\nCommands: /help /done /abort /undo /steps",
                    title=f"Training: {ft}",
                    border_style="cyan",
                )
            )

            session = TrainingSession(
                vm_manager=vm,
                playbook_service=ps,
                llm=llm,
                callbacks=callbacks,
                source_name=source,
                flow_type=ft,
                source_url=resolved_url,
                task_type=task_type if ft == "execution" else None,
                verification_model=settings.verification.model,
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

            console.print(f"[green]Playbook saved for {source} ({ft}).[/green]")

    asyncio.run(_run())


# --- train list ---


@train.command("list")
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to settings.yaml")
def train_list(config: str | None) -> None:
    """List all playbooks with health dashboard."""
    settings = load_settings(Path(config) if config else None)
    llm = LLMService(settings)
    svc = PlaybookService(settings, llm)

    site_playbooks = svc.list_site_playbooks()
    if not site_playbooks:
        console.print("[yellow]No playbooks found.[/yellow]")
        return

    now = datetime.now(UTC)
    table = Table(title="Playbook Health Dashboard")
    table.add_column("Source", style="cyan")
    table.add_column("Login", justify="center")
    table.add_column("Discovery", justify="center")
    table.add_column("Execution", justify="center")
    table.add_column("Confidence", justify="right")
    table.add_column("Last Verified", style="dim")

    for pb in site_playbooks:
        login_info = _flow_summary(pb.flows.login, now, _LOGIN_STALE_DAYS)
        disc_info = _flow_summary(pb.flows.discovery, now, _DISCOVERY_STALE_DAYS)

        exec_parts: list[str] = []
        if pb.flows.execution:
            for tt, ef in pb.flows.execution.items():
                exec_parts.append(_flow_summary(ef, now, _EXECUTION_STALE_DAYS, label=tt))
        exec_info = ", ".join(exec_parts) if exec_parts else "[dim]none[/dim]"

        # Overall confidence: average of available flows
        confs = _collect_confidences(pb)
        avg_conf = sum(confs) / len(confs) if confs else 0.0

        last_verified = _latest_verified(pb)
        lv_str = last_verified.strftime("%Y-%m-%d") if last_verified else "never"

        table.add_row(pb.source_name, login_info, disc_info, exec_info, f"{avg_conf:.0%}", lv_str)

    console.print(table)


# --- train show ---


@train.command("show")
@click.argument("source")
@click.option("--cascade", is_flag=True, help="Show full resolved cascade chain")
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to settings.yaml")
def train_show(source: str, cascade: bool, config: str | None) -> None:
    """Show playbook details for SOURCE."""
    settings = load_settings(Path(config) if config else None)
    llm = LLMService(settings)
    svc = PlaybookService(settings, llm)

    site = svc.load_site(source)
    if site is None:
        console.print(f"[red]Playbook not found for '{source}'.[/red]")
        raise SystemExit(1)

    console.print(Panel(f"Source: [bold]{site.source_name}[/]\nURL: {site.source_url}", title="Site Playbook"))

    # Display each flow
    if site.flows.login:
        _print_flow_panel("Login", site.flows.login)
    if site.flows.discovery:
        _print_flow_panel("Discovery", site.flows.discovery)
    if site.flows.execution:
        for tt, ef in site.flows.execution.items():
            _print_flow_panel(f"Execution ({tt})", ef)

    if cascade:
        console.print("\n[bold]Cascade Resolution:[/bold]")
        for ft in ("login", "discovery", "execution"):
            resolved = svc.resolve(source, host_domain=None, task_type="survey", flow_type=ft)
            if resolved:
                console.print(Panel(resolved, title=f"Resolved: {ft}", border_style="dim"))


# --- train delete ---


@train.command("delete")
@click.argument("source")
@click.option(
    "--flow", type=click.Choice(["login", "discovery", "execution"]), default=None, help="Delete specific flow"
)
@click.option("--config", type=click.Path(exists=True), default=None, help="Path to settings.yaml")
def train_delete(source: str, flow: str | None, config: str | None) -> None:
    """Delete playbook for SOURCE (or a specific flow)."""
    settings = load_settings(Path(config) if config else None)
    llm = LLMService(settings)
    svc = PlaybookService(settings, llm)

    target = f"'{source}' flow '{flow}'" if flow else f"'{source}'"
    if not click.confirm(f"Delete playbook {target}?"):
        console.print("Cancelled.")
        return

    if flow:
        site = svc.load_site(source)
        if site is None:
            console.print(f"[yellow]No playbook found for '{source}'.[/yellow]")
            return
        if flow == "login":
            site.flows.login = None
        elif flow == "discovery":
            site.flows.discovery = None
        elif flow == "execution":
            site.flows.execution = None
        svc.save_site(site)
        console.print(f"[green]Deleted {flow} flow from '{source}'.[/green]")
    else:
        deleted = svc.delete_site(source)
        if deleted:
            console.print(f"[green]Deleted playbook for '{source}'.[/green]")
        else:
            console.print(f"[yellow]No playbook found for '{source}'.[/yellow]")


# --- Helpers ---


def _flow_summary(
    flow: Any | None,
    now: datetime,
    stale_days: int,
    label: str | None = None,
) -> str:
    """Format a flow cell for the list table with step count and staleness warning."""
    if flow is None:
        return "[dim]none[/dim]"
    count = len(flow.steps) if hasattr(flow, "steps") else 0
    prefix = f"{label}: " if label else ""
    stale = ""
    if flow.verified_at and (now - flow.verified_at) > timedelta(days=stale_days):
        stale = " [red]STALE[/red]"
    return f"{prefix}{count} steps{stale}"


def _collect_confidences(pb: Any) -> list[float]:
    """Collect confidence values from all available flows."""
    confs: list[float] = []
    if pb.flows.login:
        confs.append(pb.flows.login.confidence)
    if pb.flows.discovery:
        confs.append(pb.flows.discovery.confidence)
    if pb.flows.execution:
        for ef in pb.flows.execution.values():
            confs.append(ef.confidence)
    return confs


def _latest_verified(pb: Any) -> datetime | None:
    """Find the most recent verified_at across all flows."""
    dates: list[datetime] = []
    if pb.flows.login and pb.flows.login.verified_at:
        dates.append(pb.flows.login.verified_at)
    if pb.flows.discovery and pb.flows.discovery.verified_at:
        dates.append(pb.flows.discovery.verified_at)
    if pb.flows.execution:
        for ef in pb.flows.execution.values():
            if ef.verified_at:
                dates.append(ef.verified_at)
    return max(dates) if dates else None


def _print_flow_panel(title: str, flow: Any) -> None:
    """Print a Rich panel for a flow with steps table."""
    info_lines = [
        f"Confidence: {flow.confidence:.0%}",
        f"Verified by: {flow.verified_by or 'n/a'}",
        f"Verified at: {flow.verified_at.strftime('%Y-%m-%d %H:%M') if flow.verified_at else 'never'}",
        f"Max steps: {flow.max_steps}",
    ]

    if flow.steps:
        tbl = Table(show_header=True)
        tbl.add_column("#", justify="right", style="dim", width=4)
        tbl.add_column("Instruction")
        tbl.add_column("Expected After", style="green")
        for s in flow.steps:
            tbl.add_row(str(s.seq), s.instruction, s.expected_after)
        console.print(Panel("\n".join(info_lines), title=title, border_style="cyan"))
        console.print(tbl)
    else:
        info_lines.append("No steps recorded.")
        console.print(Panel("\n".join(info_lines), title=title, border_style="cyan"))
