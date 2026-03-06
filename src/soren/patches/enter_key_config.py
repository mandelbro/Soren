"""Configuration for enter key patch behavior.

Reads enter_key_behavior from service/adapter config. Default: enter_only.
"""

from __future__ import annotations

from typing import Literal

EnterKeyBehavior = Literal["enter_only", "escape_enter"]

_default: EnterKeyBehavior = "enter_only"


def get_enter_key_behavior(service_id: str | None = None) -> EnterKeyBehavior:
    """Return enter key behavior for the given service, or default."""
    try:
        from pathlib import Path

        import yaml

        config_path = Path(__file__).resolve().parents[3] / "config" / "settings.yaml"
        if config_path.exists():
            raw = yaml.safe_load(config_path.read_text() or "{}")
            services = raw.get("services", {})
            if service_id and service_id in services:
                val = services[service_id].get("enter_key_behavior")
                if val in ("enter_only", "escape_enter"):
                    return val
            default = raw.get("enter_key_behavior")
            if default in ("enter_only", "escape_enter"):
                return default
    except Exception:
        pass
    return _default


def set_default_enter_key_behavior(behavior: EnterKeyBehavior) -> None:
    """Set default behavior (for tests)."""
    global _default
    _default = behavior
