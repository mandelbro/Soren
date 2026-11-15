"""TrainingSession — autonomous training orchestrator with verify+retry.

Implements the training loop state machine from discovery document Section 10.6:
INIT -> PREREQ_REPLAY -> NAVIGATE -> STEP_LOOP -> FINALIZING -> SAVING -> DONE.

The step loop is autonomous-first: each instruction is auto-verified via VLM
against the user-provided expected outcome, with automatic retries on failure.
Human intervention is only requested when the agent is stuck after all retries.

All user interaction is mediated through ``TrainingCallbacks``, enabling fully
deterministic testing without CLI coupling.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
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
# Each UI action requires at least 3 turns (plan → execute → completion), so 5
# was sufficient in theory but left no headroom.  10 gives breathing room while
# still bounding the loop for interactive training latency.
_SINGLE_STEP_BUDGET = 10

# Default number of auto-verify retries before falling back to human.
_DEFAULT_MAX_VERIFY_RETRIES = 3

# Seconds to wait after the CUA loop finishes before capturing the verification
# screenshot.  Animated UI changes (modals, page transitions) typically need
# 1–2 seconds to render after the triggering click.  The SDK's own
# screenshot_delay (0.5 s) is too short for these cases.
_POST_ACTION_SETTLE_DELAY = 1.5

# Seconds to wait after pressing Return at the end of auto-URL-navigation,
# before the post-navigation screenshot is captured. Page loads take
# significantly longer than in-page transitions — DNS, TLS, initial HTML,
# above-the-fold rendering. 3 seconds covers most marketing sites on a
# decent connection without making training feel sluggish.
_NAV_PAGE_LOAD_DELAY = 3.0


def _chrome_crop_active() -> bool:
    """Return True when ``SOREN_CHROME_CROP_Y`` is set to a positive integer.

    Mirrors the logic in ``soren.patches.vnc_screenshot._chrome_crop_y`` but
    returns a bool for cheap branching at prompt-assembly time. Kept here
    (rather than imported from the patch module) because the patch module is
    lazily applied and importing it at training_session load time would add
    unwanted import-time side effects.
    """
    raw = os.environ.get("SOREN_CHROME_CROP_Y", "0")
    try:
        return int(raw) > 0
    except ValueError:
        return False


# Chrome-aware system prompt prefix for UI-TARS. Only applied when
# SOREN_CHROME_CROP_Y is NOT active. When the crop is active, UI-TARS sees a
# webpage-only image and needs no chrome guidance — its "top of image" IS
# the webpage header. When the crop is off, UI-TARS sees the full screen
# including Safari's chrome and needs this prompt to avoid anchoring webpage
# elements to the top of the screen (chrome region).
#
# Ground-truth screenshots showed UI-TARS-1.5-7B systematically confusing
# Safari's browser chrome (toolbar, URL bar) with the webpage's own header —
# clicking at y~85 (inside Safari's toolbar) when asked to click a button in
# the "top-right header" of a webpage whose real header was at y~115. This
# prefix gives the model an explicit numeric boundary between browser chrome
# and webpage content. When the crop is enabled, the preprocessing does the
# job the prompt was trying to do, and leaving the prompt in would be
# actively harmful (its screen-space y-values are wrong for the cropped
# image's coordinate frame — they'd tell the model to skip the webpage
# header, which in the cropped view is at y=0-60).
_SAFARI_CHROME_PROMPT = (
    "CONTEXT: You are operating a macOS virtual machine with Safari running at "
    "1024x768 resolution. Safari's browser chrome occupies the top portion of "
    "the screen:\n"
    "  - macOS menu bar: y=0 to y=15\n"
    "  - Safari tab bar + traffic lights: y=15 to y=35\n"
    "  - Safari URL bar: y=35 to y=65\n"
    "  - Safari toolbar bottom edge: y~90\n"
    "\n"
    "IMPORTANT: The webpage's own content starts at approximately y=100. "
    "Webpage headers (with logos, navigation links, and buttons like 'Log in' "
    "or 'Sign up') live INSIDE the webpage area, typically between y=100 and "
    "y=160. When a task asks you to click a button in the page header, "
    "target y>=100 — NEVER click at y<100 unless the task is explicitly about "
    "Safari itself (e.g. 'type in the URL bar'). Clicking at y<90 will land "
    "in Safari's own toolbar, not on webpage elements.\n"
    "\n"
)


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
        # Coordinates of the last click CUA emitted inside the most recent
        # _execute_single_step call. Reset at the start of each step; read
        # by _execute_and_verify when composing retry feedback so UI-TARS
        # gets concrete info about what it tried before.
        self._last_click_xy: tuple[int, int] | None = None

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
        crop feature (SOREN_CHROME_CROP_Y=100), which hides the URL bar
        from UI-TARS's view and would otherwise make visual clicking
        impossible.
        """
        if self._source_url:
            await self._callbacks.show_message(f"Navigating to {self._source_url}...")
            try:
                await self._auto_navigate_to_url(self._source_url)
            except Exception as exc:
                await self._callbacks.show_error(f"Navigation failed: {exc}")

        screenshot, description = await self._capture_and_describe()
        await self._callbacks.show_screenshot(screenshot or b"", description)

    async def _auto_navigate_to_url(self, url: str) -> None:
        """Dispatch Cmd+L -> type URL -> Return via the CUA keyboard transport,
        then wait for the page to load before returning.

        This method deliberately does NOT go through UI-TARS or any VLM.
        It is a hardcoded sequence that simulates the keyboard shortcut a
        real human uses to navigate Safari to a URL. Dispatch happens via
        the same ``interface.hotkey`` / ``interface.type_text`` /
        ``interface.press_key`` methods that VLM-planned actions use —
        the wire signature is identical to a human keystroke because the
        transport is identical.

        Brief sleeps between steps let the UI render each state before
        the next input event, producing a more human-like timing rhythm
        (per ADR-0001 constraint: "realistic typing rhythm, standard
        hotkeys only").

        A final page-load sleep (``_NAV_PAGE_LOAD_DELAY``) gives the target
        page time to actually load before the caller captures a
        post-navigation screenshot. Without this, the "Current Screen"
        panel in the training UI displays stale pre-navigation content
        because the VNC screenshot fires before Safari has even completed
        the DNS lookup.
        """
        interface = self._computer.interface
        logger.info("Auto-navigate: dispatching Cmd+L -> type %r -> Return", url)

        # Focus the URL bar. Cmd+L is Safari's standard shortcut — every
        # Safari user has this in motor memory, so it's a valid
        # deterministic sequence under ADR-0001.
        await interface.hotkey("cmd", "l")
        await asyncio.sleep(0.3)  # let Safari render the focused URL bar state

        # Type the URL. CUA's type_text produces one keystroke per character
        # with its default timing — the result is a realistic typing rhythm
        # on the wire, not a zero-millisecond paste.
        await interface.type_text(url)
        await asyncio.sleep(0.2)

        # Submit
        await interface.press_key("return")

        # Wait for the page to load before returning control to the caller.
        # The caller (_navigate) immediately captures a screenshot via
        # _capture_and_describe — if we don't wait here, that screenshot
        # captures the pre-navigation state because Safari hasn't finished
        # even the DNS lookup yet.
        logger.info("Auto-navigate: waiting %.1fs for page to load", _NAV_PAGE_LOAD_DELAY)
        await asyncio.sleep(_NAV_PAGE_LOAD_DELAY)

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

        On retry attempts (attempt >= 2), the instruction is augmented with the
        previous verifier's failure reasoning so UI-TARS has a corrective signal
        and stops picking the same (wrong) coordinates. Without this augmentation
        the retry is effectively a no-op against deterministic models.

        Returns ``(screenshot, description, verification_passed)`` on success/acceptance,
        or ``None`` if the step was skipped.
        Raises ``_TrainingAbortRequested`` if the human chooses abort.
        """
        max_retries = self._max_verify_retries
        last_reasoning: str | None = None
        last_click_xy: tuple[int, int] | None = None
        last_screenshot: bytes = b""
        last_description: str = ""

        for attempt in range(1, max_retries + 1):
            # 1. Execute via CUA (reuses existing retry-on-exception logic).
            # On retry, thread the verifier's most recent failure reasoning AND
            # the exact pixel the previous attempt clicked into the instruction
            # so UI-TARS has a concrete corrective signal.
            if attempt > 1 and last_reasoning:
                effective_instruction = self._compose_retry_instruction(
                    instruction, attempt, max_retries, last_reasoning, last_click_xy
                )
                logger.info(
                    "Retry attempt %d/%d — augmenting instruction with verifier feedback "
                    "(%d chars reasoning, last_click=%s)",
                    attempt,
                    max_retries,
                    len(last_reasoning),
                    last_click_xy,
                )
            else:
                effective_instruction = instruction
            screenshot, description = await self._try_execute_step(effective_instruction)
            # Capture the click coordinate this attempt emitted (if any) before
            # the next iteration resets it in _execute_single_step.
            last_click_xy = self._last_click_xy
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
        """Capture a screenshot from the computer interface for verification.

        When chrome cropping is enabled for UI-TARS (via SOREN_CHROME_CROP_Y),
        UI-TARS receives a cropped image but the verifier should still see the
        full VM display including the Safari toolbar, so it retains context
        like the URL bar for verification reasoning. We prefer
        ``interface.screenshot_full`` if the VNC patch has exposed it,
        falling back to ``interface.screenshot`` for interfaces that don't
        distinguish (tests, other providers, etc.).
        """
        try:
            interface = self._computer.interface
            screenshot_fn = getattr(interface, "screenshot_full", None) or interface.screenshot
            result = await screenshot_fn()
            if isinstance(result, bytes):
                return result
            if isinstance(result, str):
                data = result.split(",", 1)[-1] if result.startswith("data:") else result
                return base64.b64decode(data)
        except Exception:
            logger.debug("Screenshot capture failed", exc_info=True)
        return None

    @staticmethod
    def _compose_retry_instruction(
        original: str,
        attempt: int,
        max_attempts: int,
        last_reasoning: str,
        last_click_xy: tuple[int, int] | None = None,
    ) -> str:
        """Build a retry instruction that feeds verifier feedback back to UI-TARS.

        The original instruction is preserved verbatim and augmented with a
        clearly delimited correction section. Only the MOST RECENT reasoning
        is included — retry context should not grow unboundedly across attempts.

        When ``last_click_xy`` is provided, the exact pixel the previous attempt
        clicked is included so UI-TARS has concrete data about what didn't work
        (rather than having to infer "something" went wrong from prose).
        """
        last_click_line = ""
        if last_click_xy is not None:
            lx, ly = last_click_xy
            last_click_line = (
                f"Your previous attempt clicked at pixel ({lx}, {ly}) and did NOT produce "
                f"the expected result. Do not click that same pixel again.\n"
            )
        return (
            f"{original}\n\n"
            f"IMPORTANT — This is retry attempt {attempt}/{max_attempts}. "
            f"Your previous attempt FAILED verification.\n"
            f"{last_click_line}"
            f"Verifier feedback on why it failed:\n"
            f"{last_reasoning}\n\n"
            f"Try a DIFFERENT element or screen location this time. "
            f"Do not repeat the same action as your previous attempt."
        )

    def _log_cua_action(self, item: dict[str, Any]) -> None:
        """Emit one INFO log line summarising a CUA ``computer_call`` item.

        The CUA SDK shape places the action dict directly under the ``action``
        key (see ``agent/responses.py::make_click_item``).  Coordinates here
        are still in VNC-image space; the rescaled screen-logical coordinates
        are logged separately by ``soren.patches.vnc_screenshot``'s click
        wrappers right before dispatch.

        Side effect: when the action is a click variant with (x, y) coords,
        updates ``self._last_click_xy`` so the retry feedback can include the
        exact pixel that was tried.
        """
        action = item.get("action")
        if not isinstance(action, dict):
            logger.info("CUA action: <unparseable item> %r", item)
            return
        action_type = action.get("type", "unknown")
        x = action.get("x")
        y = action.get("y")
        # Filter to the keys most useful for diagnosing click misses; keep the
        # whole action dict for less common types (drag, scroll, type, keypress).
        if x is not None or y is not None:
            extras = {k: v for k, v in action.items() if k not in {"type", "x", "y"}}
            extras_str = f" {extras}" if extras else ""
            logger.info("CUA action: %s x=%s y=%s%s", action_type, x, y, extras_str)
            # Track click-type actions specifically for retry feedback. Move,
            # scroll, and drag have coordinates too but aren't what we want to
            # warn UI-TARS away from on retry.
            if isinstance(action_type, str) and "click" in action_type and x is not None and y is not None:
                try:
                    self._last_click_xy = (int(x), int(y))
                except (TypeError, ValueError):
                    pass
        else:
            payload = {k: v for k, v in action.items() if k != "type"}
            logger.info("CUA action: %s %s", action_type, payload)

    async def _execute_single_step(
        self,
        instruction: str,
        computer: Any,
    ) -> tuple[bytes | None, str]:
        """Execute one instruction via CUA agent. Returns (screenshot, description).

        The CUA agent.run() generator must be consumed until it terminates or the
        step budget is reached.  A single UI action requires at least three turns:

        1. VLM plans the action — yields a ``computer_call`` item (role may be
           ``"assistant"`` here, but no screenshot yet).
        2. SDK executes the action on the VM — yields a ``computer_call_output``
           item that carries the post-action screenshot (taken ~0.5 s after the
           click, before animated UI changes finish).
        3. VLM emits a completion summary — yields a ``message`` item with
           ``role == "assistant"`` and non-empty text content.

        The break condition must only fire on Turn 3 (a ``type == "message"``
        assistant item with content), *not* on any item that merely carries
        ``role == "assistant"``.  Breaking on Turn 1 prevents the SDK from ever
        delivering the action to the VM.

        After the loop completes a post-action screenshot is captured with an
        extra 1.5-second settling delay so that animated UI changes (modals,
        page transitions) have time to finish before the verification VLM sees
        the screen.  The mid-loop screenshot is kept as a fallback in case the
        post-loop capture fails.
        """
        from soren.patches import apply_all

        apply_all()
        from cua_agent import ComputerAgent

        from soren.services.task_executor import DEFAULT_VLM_MODEL

        vlm_model = DEFAULT_VLM_MODEL

        # Fresh step — clear any click coords left over from a prior attempt
        # so retry feedback only references the CURRENT attempt's click.
        self._last_click_xy = None

        # Chrome prompt is only useful when UI-TARS is seeing the full screen
        # (chrome + webpage). When SOREN_CHROME_CROP_Y is active, the VNC
        # patch hands UI-TARS a webpage-only image, and the chrome prompt's
        # screen-space coordinates ("webpage starts at y=100", "NEVER click
        # at y<100") would tell the model to skip the webpage header — which,
        # in the cropped image, is at y=0-60. Skip the prompt in that case;
        # UI-TARS is already looking at a clean webpage image and doesn't
        # need chrome guidance to target it correctly.
        prompt_prefix = "" if _chrome_crop_active() else _SAFARI_CHROME_PROMPT
        agent = ComputerAgent(
            model=vlm_model,
            tools=[computer],
            instructions=f"{prompt_prefix}Execute this single action: {instruction}",
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
                item_type = item.get("type")
                if item_type == "computer_call":
                    # Surface the model's chosen action so coordinate misses are
                    # immediately diagnosable from the training run output. The
                    # rescaled (screen-logical) coordinates are logged separately
                    # by the click wrappers in soren.patches.vnc_screenshot.
                    self._log_cua_action(item)
                elif item_type == "computer_call_output":
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
                # Only treat a message-role assistant item as a terminal completion
                # signal when it carries text content.  A computer_call plan item
                # also carries role=="assistant" in some SDK response shapes; breaking
                # on that fires before the SDK sends the action to the VM.
                elif item.get("role") == "assistant" and item_type == "message":
                    content = item.get("content", "")
                    if content:
                        logger.debug("_execute_single_step: assistant completion message received")
                        break
            else:
                # Inner loop finished without a break — check budget and continue.
                if iterations >= _SINGLE_STEP_BUDGET:
                    logger.debug("_execute_single_step: step budget (%d) reached", _SINGLE_STEP_BUDGET)
                    break
                continue
            # Inner loop broke — exit the outer generator loop too.
            break

        # Post-action settling delay: animated UI changes (modals, page
        # transitions) often take 1–2 seconds to fully render.  The mid-loop
        # screenshot above is captured only 0.5 s after the click; capturing a
        # fresh one here gives the UI the extra time it needs.
        await asyncio.sleep(_POST_ACTION_SETTLE_DELAY)
        fresh_screenshot = await self._take_screenshot()
        if fresh_screenshot:
            logger.debug("_execute_single_step: using post-settle screenshot for verification")
            screenshot_bytes = fresh_screenshot
        else:
            logger.debug("_execute_single_step: post-settle screenshot failed, using mid-loop screenshot")

        description = ""
        if screenshot_bytes:
            description = await self._llm.generate(
                prompt="Describe what you see on this screen in one sentence.",
                images=[screenshot_bytes],
            )
        return screenshot_bytes, description
