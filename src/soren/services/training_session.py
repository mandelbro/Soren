"""TrainingSession — human-guided training orchestrator with callback-based I/O (Task 014).

Implements the training loop state machine from discovery document Section 10.6:
INIT -> PREREQ_REPLAY -> NAVIGATE -> STEP_LOOP -> FINALIZING -> SAVING -> DONE.

All user interaction is mediated through `TrainingCallbacks`, enabling fully
deterministic testing without CLI coupling.
"""

from __future__ import annotations

import base64
import logging
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

from soren.models.site_playbook import FlowPlaybook, PlaybookStep

if TYPE_CHECKING:
    from soren.services.llm_service import LLMService
    from soren.services.playbook_service import PlaybookService
    from soren.services.vm_manager import VMManager

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


# --- Callback Protocol ---


class TrainingCallbacks(Protocol):
    """I/O callbacks decoupled from CLI for testability."""

    async def show_screenshot(self, screenshot: bytes, vlm_description: str) -> None: ...
    async def get_instruction(self) -> str: ...
    async def confirm_step(self, screenshot: bytes, vlm_description: str) -> str: ...
    async def get_expected_after(self, vlm_suggestion: str) -> str: ...
    async def show_message(self, message: str) -> None: ...
    async def show_error(self, error: str) -> None: ...


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
    """Orchestrates human-guided training for a specific flow type.

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
    ) -> None:
        self._vm = vm_manager
        self._playbook_service = playbook_service
        self._llm = llm
        self._callbacks = callbacks
        self._source_name = source_name
        self._flow_type = flow_type
        self._source_url = source_url
        self._task_type = task_type

        self._state = TrainingState.INIT
        self._steps: list[PlaybookStep] = []
        self._computer: Any = None

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

    # --- STEP_LOOP ---

    async def _step_loop(self) -> str:
        """Main training loop. Returns 'done' or 'abort'."""
        while True:
            instruction = await self._callbacks.get_instruction()
            instruction = instruction.strip()

            # Handle special commands
            if instruction == "/done":
                return "done"
            if instruction == "/abort":
                return "abort"
            if instruction == "/undo":
                if self._steps:
                    removed = self._steps.pop()
                    await self._callbacks.show_message(f"Removed step {removed.seq}: {removed.instruction}")
                else:
                    await self._callbacks.show_message("No steps to undo.")
                continue
            if instruction == "/steps":
                await self._show_steps_summary()
                continue
            if instruction == "/help":
                await self._callbacks.show_message(_HELP_TEXT)
                continue

            # Execute the instruction via CUA
            screenshot, description = await self._try_execute_step(instruction)
            if screenshot is None:
                # Execution failed and user chose to skip
                continue

            # Confirm step
            confirmation = await self._callbacks.confirm_step(screenshot, description)
            confirmation = confirmation.strip().lower()

            if confirmation == "d":
                # Record step then finish
                await self._record_step(instruction, description)
                return "done"
            if confirmation != "y":
                # Discard step, continue loop
                await self._callbacks.show_message("Step discarded.")
                continue

            # Get expected_after description
            await self._record_step(instruction, description)

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
                    # Any other response means retry
                else:
                    await self._callbacks.show_error("Max retries reached. Skipping step.")
                    return None, ""
        return None, ""  # pragma: no cover

    async def _record_step(self, instruction: str, vlm_description: str) -> None:
        """Ask user for expected_after, then record the step."""
        expected_after = await self._callbacks.get_expected_after(vlm_description)
        seq = len(self._steps) + 1
        step = PlaybookStep(
            seq=seq,
            instruction=instruction,
            expected_after=expected_after.strip() if expected_after.strip() else vlm_description,
        )
        self._steps.append(step)
        await self._callbacks.show_message(f"Step {seq} recorded: {instruction}")

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
        """Execute one instruction via CUA agent. Returns (screenshot, description)."""
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
        async for result in agent.run(messages):
            output = result.get("output", [])
            for item in output:
                if isinstance(item, dict) and item.get("type") == "computer_call_output":
                    out = item.get("output", {})
                    if isinstance(out, dict):
                        img = out.get("image_url") or out.get("image") or out.get("screenshot")
                        if img:
                            if isinstance(img, str) and img.startswith("data:"):
                                img = img.split(",", 1)[-1]
                            try:
                                screenshot_bytes = base64.b64decode(img)
                            except Exception:
                                pass
            break  # Single step -- only one iteration

        description = ""
        if screenshot_bytes:
            description = await self._llm.generate(
                prompt="Describe what you see on this screen in one sentence.",
                images=[screenshot_bytes],
            )
        return screenshot_bytes, description
