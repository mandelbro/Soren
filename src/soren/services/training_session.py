"""TrainingSession — autonomous training orchestrator with verify+retry.

Implements the training loop state machine from discovery document Section 10.6:
INIT -> PREREQ_REPLAY -> NAVIGATE -> STEP_LOOP -> FINALIZING -> SAVING -> DONE.

The step loop is autonomous-first: each instruction is auto-verified via VLM
against the user-provided expected outcome, with automatic retries on failure.
Human intervention is only requested when the agent is stuck after all retries.

All user interaction is mediated through ``TrainingCallbacks``, enabling fully
deterministic testing without CLI coupling.

``TrainingSession`` is a facade: callers (``soren train run``, the meta-agent's
stuck intervention) only use this class. It delegates to:

- ``training_step_runner.TrainingStepRunner``: one CUA step, screenshots, descriptions
- ``training_verification.VerifyRetryLoop``: auto-verify, retry with feedback, human fallback
- ``training_navigation.auto_navigate_to_url``: Cmd+L navigation (ADR-0001)
- ``training_service_commands.TrainingServiceCommands``: /shortcut, /crop, ... (ADR-0002)
- ``training_prompts`` / ``training_types``: prompt text and shared types (re-exported here)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from soren.models.site_playbook import FlowPlaybook, PlaybookStep
from soren.services.training_navigation import auto_navigate_to_url
from soren.services.training_prompts import HELP_TEXT
from soren.services.training_service_commands import TrainingServiceCommands
from soren.services.training_step_runner import TrainingStepRunner
from soren.services.training_types import TrainingAbortRequested, TrainingCallbacks, TrainingState
from soren.services.training_verification import DEFAULT_MAX_VERIFY_RETRIES, VerifyRetryLoop

if TYPE_CHECKING:
    from soren.services.llm_service import LLMService
    from soren.services.playbook_service import PlaybookService
    from soren.services.service_resolution import ResolvedService
    from soren.services.service_writer import ServiceWriter
    from soren.services.vm_manager import VMManager
    from soren.verification.verifier import ActionVerifier

logger = logging.getLogger(__name__)

__all__ = ["TrainingCallbacks", "TrainingSession", "TrainingState"]


class TrainingSession:
    """Orchestrates autonomous training with verify+retry for a specific flow type.

    Parameters
    ----------
    vm_manager:
        Manages the macOS VM lifecycle (ensure ready, connect computer).
    playbook_service:
        YAML playbook CRUD and cascading resolution.
    llm:
        Ollama LLM wrapper for VLM screen descriptions.
    callbacks:
        I/O callbacks for all user interaction.
    source_name:
        The task-source (site) being trained (e.g. "betapanel").
    flow_type:
        Which flow to train: ``"login"``, ``"discovery"``, or ``"execution"``.
    source_url:
        URL to navigate to before the step loop begins.
    task_type:
        Required when *flow_type* is ``"execution"`` to key the
        ``SiteFlows.execution`` dict.
    verification_model:
        Ollama model for VLM verification (default: ``"qwen3-vl:8b"``).
    max_verify_retries:
        Number of auto-retry attempts before falling back to human.
    service:
        Resolved service config (``config/services``; ADR-0002). Active for the
        whole session, so its chrome height drives the screenshot crop and
        chrome prompt without ``SOREN_CHROME_CROP_Y``. ``None`` keeps the
        legacy env-only behavior and disables the service slash commands.
    service_writer:
        Writes service changes staged with /shortcut, /crop, ... at /done
        (default: ``config/services`` with 3 rotated backups).
    """

    def __init__(
        self,
        vm_manager: VMManager,
        playbook_service: PlaybookService,
        llm: LLMService,
        callbacks: TrainingCallbacks,
        source_name: str,
        flow_type: str,
        source_url: str = "",
        task_type: str | None = None,
        verification_model: str | None = None,
        max_verify_retries: int = DEFAULT_MAX_VERIFY_RETRIES,
        service: ResolvedService | None = None,
        service_writer: ServiceWriter | None = None,
    ) -> None:
        self._vm = vm_manager
        self._playbook_service = playbook_service
        self._llm = llm
        self._callbacks = callbacks
        self._source_name = source_name
        self._flow_type = flow_type
        self._source_url = source_url
        self._task_type = task_type
        self._verification_model = verification_model
        self._max_verify_retries = max_verify_retries
        self._service = service
        self._service_commands = TrainingServiceCommands(service)
        self._service_writer = service_writer

        self._state = TrainingState.INIT
        self._steps: list[PlaybookStep] = []
        self._computer: Any = None
        self._verifier: ActionVerifier | None = None
        self._runner = TrainingStepRunner(llm, lambda: self._service_commands.service)
        # Collaborators resolve through the session at call time, so it stays the owner
        # of step execution and the lazily built verifier.
        self._verify_loop = VerifyRetryLoop(
            callbacks,
            execute_step=lambda instruction: self._execute_single_step(instruction, self._computer),
            get_verifier=lambda: self._get_verifier(),
            last_click=lambda: self._runner.last_click_xy,
            max_retries=max_verify_retries,
        )

    # --- Public API ---

    @property
    def state(self) -> TrainingState:
        return self._state

    @property
    def steps(self) -> list[PlaybookStep]:
        return list(self._steps)

    async def run(self) -> FlowPlaybook | None:
        """Run the full training session. Returns FlowPlaybook on success, None on abort."""
        from soren.services.service_context import use_service

        try:
            with use_service(self._service):
                return await self._run_state_machine()
        except Exception as exc:
            self._state = TrainingState.ERROR
            await self._callbacks.show_error(f"Unrecoverable error: {exc}")
            logger.exception("TrainingSession fatal error")
            return None

    # --- State Machine ---

    async def _run_state_machine(self) -> FlowPlaybook | None:
        # INIT
        self._state = TrainingState.INIT
        self._computer = await self._init_vm()

        # PREREQ_REPLAY (only for discovery/execution)
        if self._flow_type in ("discovery", "execution"):
            self._state = TrainingState.PREREQ_REPLAY
            ok = await self._prereq_replay()
            if not ok:
                return None

        # NAVIGATE
        self._state = TrainingState.NAVIGATE
        await self._navigate()

        # STEP_LOOP
        self._state = TrainingState.STEP_LOOP
        result = await self._step_loop()
        if result == "abort":
            return None

        # FINALIZING
        self._state = TrainingState.FINALIZING
        await self._finalize()

        # SAVING
        self._state = TrainingState.SAVING
        playbook = await self._save()
        self._state = TrainingState.DONE
        return playbook

    # --- INIT ---

    async def _init_vm(self) -> Any:
        await self._vm.ensure_ready()
        computer = await self._vm.connect_computer()
        await self._vm.prepare_session_environment()
        return computer

    # --- PREREQ_REPLAY ---

    async def _prereq_replay(self) -> bool:
        """Replay the login flow before discovery/execution training.

        Returns True if login succeeded (or was skipped), False to abort.
        """
        site = self._playbook_service.load_site(self._source_name)
        if site is None or site.flows.login is None:
            await self._callbacks.show_message(
                f"No login playbook found for '{self._source_name}'. Continuing without login replay."
            )
            return True

        login_flow = site.flows.login
        await self._callbacks.show_message(
            f"Replaying login flow ({len(login_flow.steps)} steps) for '{self._source_name}'..."
        )
        for step in login_flow.steps:
            try:
                await self._execute_single_step(step.instruction, self._computer)
            except Exception as exc:
                await self._callbacks.show_error(f"Login replay failed at step {step.seq}: {exc}. Aborting training.")
                self._state = TrainingState.ERROR
                return False

        await self._callbacks.show_message("Login replay completed.")
        return True

    # --- NAVIGATE ---

    async def _navigate(self) -> None:
        """Navigate the VM's browser to self._source_url via a deterministic
        keyboard sequence.

        Per ADR-0001, URL navigation is a deterministic step that doesn't
        need VLM grounding: a real human user focuses the URL bar via
        Cmd+L, types the URL, and presses Enter. We dispatch exactly that
        sequence through CUA's keyboard transport, producing real OS input
        events indistinguishable from a human typing. UI-TARS is not
        involved in this step — it's the grounding engine for visual
        targets, not the mandatory router for every action.

        This also sidesteps a known incompatibility with the Safari chrome
        crop feature (service chrome_height_px / SOREN_CHROME_CROP_Y), which hides the URL bar
        from UI-TARS's view and would otherwise make visual clicking
        impossible.
        """
        if self._source_url:
            await self._callbacks.show_message(f"Navigating to {self._source_url}...")
            try:
                await auto_navigate_to_url(self._computer.interface, self._source_url)
            except Exception as exc:
                await self._callbacks.show_error(f"Navigation failed: {exc}")

        screenshot, description = await self._capture_and_describe()
        await self._callbacks.show_screenshot(screenshot or b"", description)

    async def _step_loop(self) -> str:
        """Main training loop with autonomous verify+retry. Returns 'done' or 'abort'."""
        while True:
            instruction, expected_after = await self._callbacks.get_instruction_with_expected()
            instruction = instruction.strip()

            # Handle slash commands: returns "done"/"abort" for terminal,
            # "handled" for non-terminal (/undo, /steps, /help), None if not a command.
            cmd_result = await self._handle_command(instruction)
            if cmd_result in ("done", "abort"):
                return cmd_result
            if cmd_result == "handled" or not instruction:
                continue

            # Execute + auto-verify + retry loop
            try:
                step_result = await self._verify_loop.execute_and_verify(instruction, expected_after)
            except TrainingAbortRequested:
                return "abort"

            if step_result is None:
                # Skipped by user
                continue

            screenshot, description, verified = step_result

            # Record the step
            seq = len(self._steps) + 1
            step = PlaybookStep(seq=seq, instruction=instruction, expected_after=expected_after)
            self._steps.append(step)

            status = "verified" if verified else "unverified"
            await self._callbacks.show_message(f"Step {seq} recorded ({status}): {instruction}")

    async def _handle_command(self, instruction: str) -> str | None:
        """Dispatch slash commands.

        Returns ``"done"``/``"abort"`` for terminal commands,
        ``"handled"`` for non-terminal commands (/undo, /steps, /help),
        or ``None`` if the instruction is not a slash command.
        """
        if not instruction.startswith("/"):
            return None
        if self._service_commands.handles(instruction):
            await self._callbacks.show_message(self._service_commands.handle(instruction))
            return "handled"
        cmd = instruction.lower()
        if cmd == "/done":
            return "done"
        if cmd == "/abort":
            return "abort"
        if cmd == "/undo":
            if self._steps:
                removed = self._steps.pop()
                await self._callbacks.show_message(f"Removed step {removed.seq}: {removed.instruction}")
            else:
                await self._callbacks.show_message("No steps to undo.")
        elif cmd == "/steps":
            await self._show_steps_summary()
        elif cmd == "/help":
            await self._callbacks.show_message(HELP_TEXT)
        else:
            await self._callbacks.show_message(f"Unknown command: {instruction}")
        return "handled"

    def _get_verifier(self) -> ActionVerifier:
        """Lazily construct ActionVerifier to avoid OpenCV import when unused."""
        if self._verifier is None:
            from soren.verification.baseline import BaselineManager
            from soren.verification.verifier import ActionVerifier

            self._verifier = ActionVerifier(
                baseline_manager=BaselineManager(Path("data/baselines")),
                llm_service=self._llm,
                verification_model=self._verification_model or "qwen3-vl:8b",
            )
        return self._verifier

    # --- FINALIZING ---

    async def _finalize(self) -> None:
        await self._callbacks.show_message(f"Training complete. {len(self._steps)} step(s) recorded:")
        await self._show_steps_summary()

    # --- SAVING ---

    async def _save(self) -> FlowPlaybook:
        playbook = FlowPlaybook(
            verified_at=datetime.now(UTC),
            verified_by="human",
            confidence=0.5,
            steps=self._steps,
            max_steps=len(self._steps) + 5,
        )
        self._playbook_service.save_flow(
            self._source_name,
            self._flow_type,
            playbook,
            self._task_type,
        )
        await self._callbacks.show_message(f"Playbook saved for '{self._source_name}' ({self._flow_type}).")
        await self._save_service_updates()
        return playbook

    async def _save_service_updates(self) -> None:
        """Confirm and write service changes staged during the session. Never fails the session."""
        from soren.services.service_writer import ServiceWriter

        writer = self._service_writer or ServiceWriter()
        await self._service_commands.save(self._callbacks, writer, self._source_url)

    # --- Helpers ---

    async def _show_steps_summary(self) -> None:
        if not self._steps:
            await self._callbacks.show_message("No steps recorded yet.")
            return
        lines = [f"  {s.seq}. {s.instruction} -> {s.expected_after}" for s in self._steps]
        await self._callbacks.show_message("\n".join(lines))

    # --- Step execution (delegated to TrainingStepRunner) ---

    async def _execute_single_step(self, instruction: str, computer: Any) -> tuple[bytes | None, str]:
        """Execute one instruction via a CUA agent. Returns (screenshot, description)."""
        return await self._runner.run_step(instruction, computer)

    async def _capture_and_describe(self) -> tuple[bytes | None, str]:
        """Take a screenshot and get a VLM description of the current screen."""
        return await self._runner.capture_and_describe(self._computer)
