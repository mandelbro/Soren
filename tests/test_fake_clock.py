"""Tests for the test-suite virtual clock (tests/fake_clock.py)."""

from __future__ import annotations

import asyncio
import time
import types

from tests.fake_clock import FakeClock


def _module_under_test() -> types.ModuleType:
    mod = types.ModuleType("fake_mod")
    mod.asyncio = asyncio  # type: ignore[attr-defined]
    mod.time = time  # type: ignore[attr-defined]

    async def wait_until(deadline_s: float, poll: float) -> int:
        start = mod.time.monotonic()  # type: ignore[attr-defined]
        polls = 0
        while mod.time.monotonic() - start < deadline_s:  # type: ignore[attr-defined]
            polls += 1
            await mod.asyncio.sleep(poll)  # type: ignore[attr-defined]
        return polls

    mod.wait_until = wait_until  # type: ignore[attr-defined]
    return mod


class TestFakeClock:
    async def test_deadline_loop_runs_same_iterations_instantly(self, monkeypatch) -> None:
        mod = _module_under_test()
        clock = FakeClock().install(monkeypatch, mod)
        started = time.perf_counter()
        assert await mod.wait_until(120, 15) == 8
        assert time.perf_counter() - started < 1
        assert clock.sleeps == [15] * 8

    async def test_still_yields_to_event_loop(self, monkeypatch) -> None:
        mod = _module_under_test()
        FakeClock().install(monkeypatch, mod)
        order: list[str] = []

        async def other() -> None:
            order.append("other")

        task = asyncio.create_task(other())
        await mod.asyncio.sleep(10)  # type: ignore[attr-defined]
        order.append("after sleep")
        await task
        assert order == ["other", "after sleep"]

    async def test_other_attributes_and_modules_untouched(self, monkeypatch) -> None:
        mod = _module_under_test()
        FakeClock().install(monkeypatch, mod)
        assert mod.asyncio.gather is asyncio.gather  # type: ignore[attr-defined]
        assert asyncio.sleep is not mod.asyncio.sleep  # type: ignore[attr-defined]
        assert time.monotonic() != mod.time.monotonic()  # type: ignore[attr-defined]
