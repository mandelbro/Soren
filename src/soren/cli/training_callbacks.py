"""RichTrainingCallbacks — Rich terminal UI for interactive training sessions."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from soren.services.training_session import TrainingCallbacks

console = Console()


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

    async def confirm_service_update(self, path: Path, changes: list[str], warnings: list[str]) -> bool:
        body = "\n".join(f"• {escape(c)}" for c in changes)
        if warnings:
            body += "\n\n[yellow]Warnings:[/yellow]\n" + "\n".join(f"⚠ {escape(w)}" for w in warnings)
        console.print(Panel(body, title=f"Service changes → {escape(str(path))}", border_style="cyan"))
        answer = console.input("Save these service changes? [Y/n]: ").strip().lower()
        return answer in ("", "y", "yes")
