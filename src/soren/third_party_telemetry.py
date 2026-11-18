"""Default third-party (CUA SDK) telemetry to off.

The CUA SDK (``cua_agent``, ``computer``) sends PostHog product analytics, and
optionally OpenTelemetry, from the machine that runs it: at import time and
when an agent starts. Soren is a local-first tool (see docs/prd.md), so
telemetry is off unless the user opts in with ``CUA_TELEMETRY=on`` in the
environment or ``.env``. Disabling it also removes the blocking network calls
from SDK import.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping

CUA_TELEMETRY_ENV_VAR = "CUA_TELEMETRY"


def default_third_party_telemetry_off(environ: MutableMapping[str, str] | None = None) -> None:
    """Set ``CUA_TELEMETRY=off`` unless the user already chose a value.

    Call after ``.env`` has been loaded so an opt-in there is respected.
    """
    env = os.environ if environ is None else environ
    env.setdefault(CUA_TELEMETRY_ENV_VAR, "off")
