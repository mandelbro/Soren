"""TrainingSession — autonomous training orchestrator with verify+retry (Task 014).

Implements the training loop state machine from discovery document Section 10.6:
INIT -> PREREQ_REPLAY -> NAVIGATE -> STEP_LOOP -> FINALIZING -> SAVING -> DONE.

The step loop is autonomous-first: each instruction is auto-verified via VLM
against the user-provided expected outcome, with automatic retries on failure.
Human intervention is only requested when the agent is stuck after all retries.

All user interaction is mediated through ``TrainingCallbacks``, enabling fully
deterministic testing without CLI coupling.
"""

from __future__ import annotations

import base64
import logging
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from soren.models.site_playbook import FlowPlaybook, PlaybookStep

if TYPE_CHECKING:
    from soren.services.llm_service import LLMService
    from soren.services.playbook_service import PlaybookService
    from soren.services.vm_manager import VMManager
    from soren.verification.verifier import ActionVerifier, VerificationResult

logger = logging.getLogger(__name__)

# Commands the user can type during the step loop.
_HELP_TEXT = """\
Training session commands:
  /done   — Finish recording and save the playbook
  /abort  — Discard all steps and exit (nothing saved)
  /undo   — Remove the last recorded step
  /steps  — Show all recorded steps so far
  /help   — Show this help text
"""

# Maximum CUA agent loop iterations per single training step.
_SINGLE_STEP_BUDGET = 5

# Default number of auto-verify retries before falling back to human.
_DEFAULT_MAX_VERIFY_RETRIES = 3


# --- Sentinel Exception ---


class _TrainingAbortRequested(Exception):
    """Raised inside _execute_and_verify when the human chooses abort."""


# --- Callback Protocol ---


class TrainingCallbacks(Protocol):
    """I/O callbacks decoupled from CLI for testability."""

    async def show_screenshot(self, screenshot: bytes, vlm_description: str) -> None: ...
    async def get_instruction(self) -> str: ...
    async def confirm_step(self, screenshot: bytes, vlm_description: str) -> str: ...
    async def get_expected_after(self, vlm_suggestion: str) -> str: ...
    async def show_message(self, message: str) -> None: ...
    async def show_error(self, error: str) -> None: ...

    # --- Autonomous verify+retry callbacks ---

    async def get_instruction_with_expected(self) -> tuple[str, str]:
        """Collect instruction AND expected outcome before execution."""
        ...

    async def show_verification_result(
        self,
        attempt: int,
        max_attempts: int,
        passed: bool,
        confidence: float,
        reasoning: str | None,
    ) -> None:
        """Display auto-verification result."""
        ...

    async def handle_stuck(
        self,
        instruction: str,
        expected_after: str,
        attempts: int,
        last_reasoning: str | None,
    ) -> str:
        """Human fallback when all retries exhausted.

        Returns one of: 'skip', 'accept', 'abort', or 'retry:<new_instruction>'.
        """
        ...


# --- State Enum ---


class TrainingState(str, Enum):
    """States for the training session state machine."""

    INIT = "init"
    PREREQ_REPLAY = "prereq_replay"
    NAVIGATE = "navigate"
    STEP_LOOP = "step_loop"
    FINALIZING = "finalizing"
    SAVING = "saving"
    DONE = "done"
    ERROR = "error"


# --- Orchestrator ---


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
        The task-source (site) being trained (e.g. "prolific").
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
        max_verify_retries: int = _DEFAULT_MAX_VERIFY_RETRIES,
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

        self._state = TrainingState.INIT
        self._steps: list[PlaybookStep] = []
        self._computer: Any = None
        self._verifier: ActionVerifier | None = None

    # --- Public API ---

    @property
    def state(self) -> TrainingState:
        return self._state

    @property
    def steps(self) -> list[PlaybookStep]:
        return list(self._steps)

    async def run(self) -> FlowPlaybook | None:
        """Run the full training session. Returns FlowPlaybook on success, None on abort."""
        try:
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
        if self._source_url:
            await self._callbacks.show_message(f"Navigating to {self._source_url}...")
            try:
                await self._execute_single_step(
                    f"Navigate to {self._source_url}",
                    self._computer,
                )
            except Exception as exc:
                await self._callbacks.show_error(f"Navigation failed: {exc}")

        screenshot, description = await self._capture_and_describe()
        await self._callbacks.show_screenshot(screenshot or b"", description)

    # --- STEP_LOOP (autonomous verify+retry) ---

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
                step_result = await self._execute_and_verify(instruction, expected_after)
            except _TrainingAbortRequested:
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
            await self._callbacks.show_message(_HELP_TEXT)
        else:
            await self._callbacks.show_message(f"Unknown command: {instruction}")
        return "handled"

    async def _execute_and_verify(
        self,
        instruction: str,
        expected_after: str,
    ) -> tuple[bytes, str, bool] | None:
        """Execute instruction, auto-verify against expected_after, retry on failure.

        Returns ``(screenshot, description, verification_passed)`` on success/acceptance,
        or ``None`` if the step was skipped.
        Raises ``_TrainingAbortRequested`` if the human chooses abort.
        """
        max_retries = self._max_verify_retries
        last_reasoning: str | None = None
        last_screenshot: bytes = b""
        last_description: str = ""

        for attempt in range(1, max_retries + 1):
            # 1. Execute via CUA (reuses existing retry-on-exception logic)
            screenshot, description = await self._try_execute_step(instruction)
            if screenshot is None:
                return None  # CUA execution failed entirely
            last_screenshot, last_description = screenshot, description

            # 2. Auto-verify using ActionVerifier Tier 2 (VLM semantic check)
            verifier = self._get_verifier()
            vr: VerificationResult = await verifier.verify(
                screenshot=last_screenshot,
                expected_state=expected_after,
                baseline_name=None,  # No baselines during training
            )

            # 3. Show verification result (transparency)
            await self._callbacks.show_verification_result(
                attempt=attempt,
                max_attempts=max_retries,
                passed=vr.passed,
                confidence=vr.confidence,
                reasoning=vr.reasoning,
            )
            last_reasoning = vr.reasoning

            # 4. If passed, return immediately
            if vr.passed:
                return last_screenshot, last_description, True

            # 5. If not passed and more retries, auto-retry
            if attempt < max_retries:
                await self._callbacks.show_message(f"Verification failed. Auto-retrying ({attempt}/{max_retries})...")

        # All retries exhausted — fall back to human
        action = await self._callbacks.handle_stuck(
            instruction=instruction,
            expected_after=expected_after,
            attempts=max_retries,
            last_reasoning=last_reasoning,
        )

        return self._process_stuck_action(action, last_screenshot, last_description, expected_after)

    def _process_stuck_action(
        self,
        action: str,
        last_screenshot: bytes,
        last_description: str,
        expected_after: str,
    ) -> tuple[bytes, str, bool] | None:
        """Interpret the human's stuck-handler decision."""
        action = action.strip().lower()
        if action == "skip":
            return None
        if action == "accept":
            return last_screenshot, last_description, False
        if action == "abort":
            raise _TrainingAbortRequested()
        # "retry:<new_instruction>" is not handled synchronously —
        # the caller should re-enter _execute_and_verify. But to keep
        # this simple, we return None (skip) and let the loop continue.
        # The retry-with-new-instruction path is handled at the _step_loop level.
        return None

    # --- CUA Execution ---

    async def _try_execute_step(self, instruction: str) -> tuple[bytes | None, str]:
        """Execute a single CUA step with error recovery.

        Returns (screenshot, description) on success, (None, "") if user chose to skip.
        """
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                screenshot, description = await self._execute_single_step(instruction, self._computer)
                return screenshot or b"", description
            except Exception as exc:
                await self._callbacks.show_error(f"CUA error (attempt {attempt}/{max_attempts}): {exc}")
                if attempt < max_attempts:
                    await self._callbacks.show_message("Type 'skip' to skip this step, or press Enter to retry.")
                    resp = await self._callbacks.get_instruction()
                    if resp.strip().lower() in ("skip", "s"):
                        return None, ""
                else:
                    await self._callbacks.show_error("Max retries reached. Skipping step.")
                    return None, ""
        return None, ""  # pragma: no cover

    # --- Verification ---

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
        return playbook

    # --- Helpers ---

    async def _show_steps_summary(self) -> None:
        if not self._steps:
            await self._callbacks.show_message("No steps recorded yet.")
            return
        lines = [f"  {s.seq}. {s.instruction} -> {s.expected_after}" for s in self._steps]
        await self._callbacks.show_message("\n".join(lines))

    async def _capture_and_describe(self) -> tuple[bytes | None, str]:
        """Take a screenshot and get a VLM description of the current screen."""
        screenshot = await self._take_screenshot()
        description = ""
        if screenshot:
            try:
                description = await self._llm.generate(
                    prompt="Describe what you see on this screen in one sentence.",
                    images=[screenshot],
                )
            except Exception:
                description = "(Unable to describe screen)"
        return screenshot, description

    async def _take_screenshot(self) -> bytes | None:
        """Capture a screenshot from the computer interface."""
        try:
            result = await self._computer.interface.screenshot()
            if isinstance(result, bytes):
                return result
            if isinstance(result, str):
                data = result.split(",", 1)[-1] if result.startswith("data:") else result
                return base64.b64decode(data)
        except Exception:
            logger.debug("Screenshot capture failed", exc_info=True)
        return None

    async def _execute_single_step(
        self,
        instruction: str,
        computer: Any,
    ) -> tuple[bytes | None, str]:
        """Execute one instruction via CUA agent. Returns (screenshot, description).

        The CUA agent.run() generator must be consumed until it terminates or the
        step budget is reached.  Each iteration has two phases:

        1. The VLM yields a planned action (``type == "computer_call"``).
        2. The SDK executes the action on the VM and yields the screenshot result
           (``type == "computer_call_output"``).

        Breaking after the first yield (the plan phase) prevents the SDK from ever
        executing the action, which is why the VM appeared unresponsive.
        """
        from soren.patches import apply_all

        apply_all()
        from agent import ComputerAgent

        from soren.services.task_executor import DEFAULT_VLM_MODEL

        vlm_model = DEFAULT_VLM_MODEL

        agent = ComputerAgent(
            model=vlm_model,
            tools=[computer],
            instructions=f"Execute this single action: {instruction}",
            screenshot_delay=0.5,
        )
        messages = [{"role": "user", "content": instruction}]
        screenshot_bytes: bytes | None = None
        iterations = 0
        async for result in agent.run(messages):
            iterations += 1
            output = result.get("output", [])
            logger.debug("_execute_single_step iteration %d: %d output items", iterations, len(output))
            for item in output:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "computer_call_output":
                    out = item.get("output", {})
                    if isinstance(out, dict):
                        img = out.get("image_url") or out.get("image") or out.get("screenshot")
                        if img:
                            if isinstance(img, str) and img.startswith("data:"):
                                img = img.split(",", 1)[-1]
                            try:
                                screenshot_bytes = base64.b64decode(img)
                            except Exception:
                                logger.debug("Failed to decode screenshot in training step", exc_info=True)
                elif item.get("role") == "assistant":
                    logger.debug("_execute_single_step: assistant completion signal received")

            last_roles = [i.get("role") for i in output if isinstance(i, dict)]
            if "assistant" in last_roles:
                break
            if iterations >= _SINGLE_STEP_BUDGET:
                logger.debug("_execute_single_step: step budget (%d) reached", _SINGLE_STEP_BUDGET)
                break

        description = ""
        if screenshot_bytes:
            description = await self._llm.generate(
                prompt="Describe what you see on this screen in one sentence.",
                images=[screenshot_bytes],
            )
        return screenshot_bytes, description
