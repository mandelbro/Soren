"""Prompt text for training: help, the Safari chrome prompt, and retry instructions."""

from __future__ import annotations

from soren.services.training_service_commands import SERVICE_COMMANDS_HELP

# Commands the user can type during the step loop.
HELP_TEXT = (
    """\
Training session commands:
  /done   — Finish recording and save the playbook
  /abort  — Discard all steps and exit (nothing saved)
  /undo   — Remove the last recorded step
  /steps  — Show all recorded steps so far
  /help   — Show this help text

"""
    + SERVICE_COMMANDS_HELP
)


def chrome_crop_active() -> bool:
    """Return True when screenshots handed to UI-TARS are chrome-cropped.

    Uses the same reader as ``soren.patches.vnc_screenshot._chrome_crop_y``
    (the active service's ``chrome_height_px``, else ``SOREN_CHROME_CROP_Y``),
    so the prompt decision always matches what the model actually sees.
    """
    from soren.services.service_context import current_chrome_height_px

    return current_chrome_height_px() > 0


# Chrome-aware system prompt prefix for UI-TARS. Only applied when
# the chrome crop is NOT active (service chrome_height_px or SOREN_CHROME_CROP_Y).
# When the crop is active, UI-TARS sees a
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
SAFARI_CHROME_PROMPT = (
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


def compose_retry_instruction(
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
