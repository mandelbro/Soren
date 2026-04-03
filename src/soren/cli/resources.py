"""Resources command group."""

import click
from rich.console import Console

from soren.services.meta_agent import MEMORY_ABORT_THRESHOLD_PCT


@click.group()
def resources():
    """Resource validation and memory checks."""


@resources.command("check")
def resources_check():
    """Validate memory and Ollama config. Run before begin-soren-cycle."""
    import os

    import psutil

    from soren.services.config_loader import load_settings

    console = Console()
    issues: list[str] = []
    warnings: list[str] = []

    settings = load_settings()
    is_cloud = settings.llm.is_cloud_model

    # Memory — threshold matches the pre-cycle abort in SorenMetaAgent
    vm = psutil.virtual_memory()
    total_gb = vm.total / (1024**3)
    used_pct = vm.percent
    if total_gb < 32:
        warnings.append(f"Machine has {total_gb:.1f} GB RAM; 32 GB recommended for full cycle.")
    if used_pct >= MEMORY_ABORT_THRESHOLD_PCT:
        issues.append(
            f"Memory at {used_pct:.1f}% (threshold {MEMORY_ABORT_THRESHOLD_PCT:.0f}%) — close apps before running cycle."
        )

    # Ollama env — only enforced for local models
    if not is_cloud:
        max_models = os.environ.get("OLLAMA_MAX_LOADED_MODELS")
        if max_models != "1":
            issues.append(
                "OLLAMA_MAX_LOADED_MODELS should be 1 for local models (prevents OOM). "
                "Set in .env and run: source .env && ollama serve"
            )
        keep_alive = os.environ.get("OLLAMA_KEEP_ALIVE")
        if keep_alive and keep_alive != "0":
            warnings.append(f"OLLAMA_KEEP_ALIVE={keep_alive}; 0 recommended for local model unload.")
    else:
        console.print(f"[dim]Cloud model detected ({settings.llm.primary_model}) — Ollama env checks skipped.[/dim]")

    if issues:
        console.print("[red]Issues:[/red]")
        for m in issues:
            console.print(f"  \u2022 {m}")
    if warnings:
        console.print("[yellow]Warnings:[/yellow]")
        for m in warnings:
            console.print(f"  \u2022 {m}")
    if not issues and not warnings:
        console.print("[green]OK[/green] \u2014 Memory and Ollama config look good.")
    elif not issues:
        console.print("[green]OK[/green] \u2014 No blocking issues.")
    else:
        raise SystemExit(1)
