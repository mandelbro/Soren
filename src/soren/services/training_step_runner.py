"""TrainingStepRunner — runs one training instruction through a fresh CUA agent.

Owns the UI-TARS call for a single step: the chrome-aware prompt, consuming the
agent's output until it completes (bounded by SINGLE_STEP_BUDGET), logging each
action, a settle delay, and the post-step screenshot and description used for
verification. Records the last click coordinate for retry feedback.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from soren.services.agent_output import screenshot_from_item
from soren.services.training_prompts import SAFARI_CHROME_PROMPT, chrome_crop_active

if TYPE_CHECKING:
    from soren.services.llm_service import LLMService
    from soren.services.service_resolution import ResolvedService

logger = logging.getLogger(__name__)

# Maximum CUA agent loop iterations per single training step.
# Each UI action requires at least 3 turns (plan → execute → completion), so 5
# was sufficient in theory but left no headroom.  10 gives breathing room while
# still bounding the loop for interactive training latency.
SINGLE_STEP_BUDGET = 10

# Seconds to wait after the CUA loop finishes before capturing the verification
# screenshot.  Animated UI changes (modals, page transitions) typically need
# 1–2 seconds to render after the triggering click.  The SDK's own
# screenshot_delay (0.5 s) is too short for these cases.
POST_ACTION_SETTLE_DELAY = 1.5


class TrainingStepRunner:
    """Executes single training steps and captures screens for verification."""

    def __init__(self, llm: LLMService, live_service: Callable[[], ResolvedService | None]) -> None:
        self._llm = llm
        self._live_service = live_service
        # Coordinates of the last click CUA emitted in the most recent step; reset at the
        # start of each step and read by the verify/retry loop to compose retry feedback.
        self.last_click_xy: tuple[int, int] | None = None

    async def run_step(self, instruction: str, computer: Any) -> tuple[bytes | None, str]:
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
        from soren.services.service_context import use_service

        # The live service reflects /crop changes made earlier in the session.
        with use_service(self._live_service()):
            return await self._run(instruction, computer)

    async def _run(self, instruction: str, computer: Any) -> tuple[bytes | None, str]:
        from soren.patches import apply_all

        apply_all()
        from cua_agent import ComputerAgent

        from soren.services.task_executor import DEFAULT_VLM_MODEL

        vlm_model = DEFAULT_VLM_MODEL

        # Fresh step — clear any click coords left over from a prior attempt
        # so retry feedback only references the CURRENT attempt's click.
        self.last_click_xy = None

        # Chrome prompt is only useful when UI-TARS is seeing the full screen
        # (chrome + webpage). When the chrome crop is active, the VNC
        # patch hands UI-TARS a webpage-only image, and the chrome prompt's
        # screen-space coordinates ("webpage starts at y=100", "NEVER click
        # at y<100") would tell the model to skip the webpage header — which,
        # in the cropped image, is at y=0-60. Skip the prompt in that case;
        # UI-TARS is already looking at a clean webpage image and doesn't
        # need chrome guidance to target it correctly.
        prompt_prefix = "" if chrome_crop_active() else SAFARI_CHROME_PROMPT
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
                    decoded = screenshot_from_item(item)
                    if decoded:  # keep the previous screenshot if this one can't be decoded
                        screenshot_bytes = decoded
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
                if iterations >= SINGLE_STEP_BUDGET:
                    logger.debug("_execute_single_step: step budget (%d) reached", SINGLE_STEP_BUDGET)
                    break
                continue
            # Inner loop broke — exit the outer generator loop too.
            break

        # Post-action settling delay: animated UI changes (modals, page
        # transitions) often take 1–2 seconds to fully render.  The mid-loop
        # screenshot above is captured only 0.5 s after the click; capturing a
        # fresh one here gives the UI the extra time it needs.
        await asyncio.sleep(POST_ACTION_SETTLE_DELAY)
        fresh_screenshot = await self.take_screenshot(computer)
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
                    self.last_click_xy = (int(x), int(y))
                except (TypeError, ValueError):
                    pass
        else:
            payload = {k: v for k, v in action.items() if k != "type"}
            logger.info("CUA action: %s %s", action_type, payload)

    async def take_screenshot(self, computer: Any) -> bytes | None:
        """Capture a screenshot from the computer interface for verification.

        When chrome cropping is enabled for UI-TARS (service chrome_height_px or SOREN_CHROME_CROP_Y),
        UI-TARS receives a cropped image but the verifier should still see the
        full VM display including the Safari toolbar, so it retains context
        like the URL bar for verification reasoning. We prefer
        ``interface.screenshot_full`` if the VNC patch has exposed it,
        falling back to ``interface.screenshot`` for interfaces that don't
        distinguish (tests, other providers, etc.).
        """
        try:
            interface = computer.interface
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

    async def capture_and_describe(self, computer: Any) -> tuple[bytes | None, str]:
        """Take a screenshot and get a VLM description of the current screen."""
        screenshot = await self.take_screenshot(computer)
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
