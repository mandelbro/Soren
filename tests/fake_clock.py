"""Virtual clock for tests of code that sleeps or polls against time.monotonic().

``install(monkeypatch, *modules)`` swaps each module's ``asyncio`` and ``time``
references for proxies whose ``sleep``/``monotonic`` use the fake clock; every
other attribute delegates to the real module. Only the module-under-test is
affected: the event loop (which uses the real ``time.monotonic``) and other
modules keep real time. ``sleep`` advances the clock instantly but still yields
to the event loop, so cooperative scheduling behaves as in production, and
deadline loops (``while time.monotonic() - start < timeout``) terminate after
the same number of iterations they would in real time.
"""

from __future__ import annotations

import asyncio
import time
from types import ModuleType
from typing import Any

import pytest

_REAL_SLEEP = asyncio.sleep


class _ModuleProxy:
    def __init__(self, real: ModuleType, **overrides: Any) -> None:
        self._real = real
        self.__dict__.update(overrides)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float, result: Any = None) -> Any:
        self.sleeps.append(delay)
        self.now += max(0.0, float(delay))
        await _REAL_SLEEP(0)  # still yield to the event loop
        return result

    def install(self, monkeypatch: pytest.MonkeyPatch, *modules: ModuleType) -> FakeClock:
        for module in modules:
            if isinstance(getattr(module, "asyncio", None), ModuleType):
                monkeypatch.setattr(module, "asyncio", _ModuleProxy(asyncio, sleep=self.sleep))
            if isinstance(getattr(module, "time", None), ModuleType):
                monkeypatch.setattr(module, "time", _ModuleProxy(time, monotonic=self.monotonic))
        return self
