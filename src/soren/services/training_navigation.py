"""Training auto-navigation: Cmd+L, type the URL, Return (ADR-0001).

A deterministic keyboard sequence sent through CUA's real-OS-input
transport, not through UI-TARS, so it works even when the chrome crop hides
the URL bar.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Seconds to wait after pressing Return at the end of auto-URL-navigation,
# before the post-navigation screenshot is captured. Page loads take
# significantly longer than in-page transitions — DNS, TLS, initial HTML,
# above-the-fold rendering. 3 seconds covers most marketing sites on a
# decent connection without making training feel sluggish.
NAV_PAGE_LOAD_DELAY = 3.0


async def auto_navigate_to_url(interface: Any, url: str) -> None:
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

    A final page-load sleep (``NAV_PAGE_LOAD_DELAY``) gives the target
    page time to actually load before the caller captures a
    post-navigation screenshot. Without this, the "Current Screen"
    panel in the training UI displays stale pre-navigation content
    because the VNC screenshot fires before Safari has even completed
    the DNS lookup.
    """
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
    # The caller (TrainingSession._navigate) immediately captures a screenshot via
    # _capture_and_describe — if we don't wait here, that screenshot
    # captures the pre-navigation state because Safari hasn't finished
    # even the DNS lookup yet.
    logger.info("Auto-navigate: waiting %.1fs for page to load", NAV_PAGE_LOAD_DELAY)
    await asyncio.sleep(NAV_PAGE_LOAD_DELAY)
