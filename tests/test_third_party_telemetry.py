"""Soren defaults the CUA SDK's telemetry (PostHog/OpenTelemetry) to off, unless the user opts in."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from soren.third_party_telemetry import default_third_party_telemetry_off


class TestDefault:
    def test_sets_off_when_unset(self) -> None:
        env: dict[str, str] = {}
        default_third_party_telemetry_off(env)
        assert env == {"CUA_TELEMETRY": "off"}

    @pytest.mark.parametrize("value", ["on", "1", "off", ""])
    def test_explicit_value_respected(self, value: str) -> None:
        env = {"CUA_TELEMETRY": value}
        default_third_party_telemetry_off(env)
        assert env == {"CUA_TELEMETRY": value}


class TestCliEntryPoint:
    """Run in a fresh interpreter: the test suite itself sets CUA_TELEMETRY in tests/conftest.py."""

    @staticmethod
    def _value_after_importing_cli(extra_env: dict[str, str]) -> str:
        env = {k: v for k, v in os.environ.items() if k != "CUA_TELEMETRY"} | extra_env
        code = "import os, soren.cli; print(os.environ.get('CUA_TELEMETRY', '<unset>'))"
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
        return out.stdout.strip().splitlines()[-1]

    def test_cli_defaults_telemetry_off(self) -> None:
        assert self._value_after_importing_cli({}) == "off"

    def test_cli_respects_explicit_opt_in(self) -> None:
        assert self._value_after_importing_cli({"CUA_TELEMETRY": "on"}) == "on"
