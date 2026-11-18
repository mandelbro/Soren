"""Tests for the per-task active service context and helpers that read it."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from soren.models.persona import Persona
from soren.models.service import ServiceConfigError
from soren.models.task import Task
from soren.models.task_source import TaskSource
from soren.services.config_loader import SorenSettings
from soren.services.service_context import current_chrome_height_px, get_active_service, use_service
from soren.services.service_registry import ServiceRegistry
from soren.services.service_resolution import ResolvedService, resolve_for_task, resolve_service

NO_ENV: dict[str, str] = {}


def _resolved(tmp_path: Path, name: str = "svc", file_text: str | None = None, **kwargs) -> ResolvedService:
    services_dir = tmp_path / "services"
    services_dir.mkdir(exist_ok=True)
    if file_text is not None:
        (services_dir / f"{name}.yaml").write_text(file_text, encoding="utf-8")
    return resolve_service(
        name,
        registry=ServiceRegistry(services_dir),
        settings=kwargs.pop("settings", SorenSettings()),
        environ=kwargs.pop("environ", NO_ENV),
        **kwargs,
    )


def _task(source_name: str = "acmepanel") -> Task:
    return Task(id="t1", dedup_key="k", source_name=source_name)


class TestUseService:
    def test_nothing_active_by_default(self) -> None:
        assert get_active_service() is None

    def test_sets_and_restores(self, tmp_path: Path) -> None:
        svc = _resolved(tmp_path)
        with use_service(svc) as active:
            assert active is svc
            assert get_active_service() is svc
        assert get_active_service() is None

    def test_nested_restores_outer(self, tmp_path: Path) -> None:
        outer, inner = _resolved(tmp_path, "outer"), _resolved(tmp_path, "inner")
        with use_service(outer):
            with use_service(inner):
                assert get_active_service() is inner
            assert get_active_service() is outer

    def test_none_explicitly_clears(self, tmp_path: Path) -> None:
        with use_service(_resolved(tmp_path)):
            with use_service(None):
                assert get_active_service() is None

    def test_restored_after_exception(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError):
            with use_service(_resolved(tmp_path)):
                raise RuntimeError("boom")
        assert get_active_service() is None

    async def test_visible_in_awaited_calls_and_child_tasks(self, tmp_path: Path) -> None:
        svc = _resolved(tmp_path)

        async def read() -> ResolvedService | None:
            await asyncio.sleep(0)
            return get_active_service()

        async def agen():
            yield get_active_service()

        with use_service(svc):
            assert await read() is svc
            assert await asyncio.create_task(read()) is svc
            assert [x async for x in agen()] == [svc]

    async def test_not_leaked_to_sibling_tasks(self, tmp_path: Path) -> None:
        svc = _resolved(tmp_path)
        started = asyncio.Event()
        seen: list[ResolvedService | None] = []

        async def sibling() -> None:
            await started.wait()
            seen.append(get_active_service())

        task = asyncio.create_task(sibling())  # created outside the context
        with use_service(svc):
            started.set()
            await task
        assert seen == [None]


class TestCurrentChromeHeight:
    def test_zero_with_nothing_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SOREN_CHROME_CROP_Y", raising=False)
        assert current_chrome_height_px() == 0

    def test_active_service_value_without_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SOREN_CHROME_CROP_Y", raising=False)
        with use_service(_resolved(tmp_path, file_text="config:\n  chrome_height_px: 100\n")):
            assert current_chrome_height_px() == 100

    @pytest.mark.parametrize(("raw", "expected"), [("80", 80), ("-3", 0), ("junk", 0)])
    def test_legacy_env_only_behavior(self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: int) -> None:
        """With no active service, the env var behaves exactly as before (invalid -> 0)."""
        monkeypatch.setenv("SOREN_CHROME_CROP_Y", raw)
        assert current_chrome_height_px() == expected

    def test_active_service_carries_env_override_from_resolution(self, tmp_path: Path) -> None:
        svc = _resolved(tmp_path, file_text="config:\n  chrome_height_px: 100\n", environ={"SOREN_CHROME_CROP_Y": "40"})
        with use_service(svc):
            assert current_chrome_height_px() == 40

    def test_malformed_env_falls_through_to_service(self, tmp_path: Path) -> None:
        svc = _resolved(tmp_path, file_text="config:\n  chrome_height_px: 100\n", environ={"SOREN_CHROME_CROP_Y": "x"})
        with use_service(svc):
            assert current_chrome_height_px() == 100


class TestEnvOverrideWarning:
    LOGGER = "soren.services.service_resolution"

    def test_warns_when_env_overrides_different_service_value(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            _resolved(tmp_path, file_text="config:\n  chrome_height_px: 100\n", environ={"SOREN_CHROME_CROP_Y": "40"})
        messages = [r.getMessage() for r in caplog.records]
        assert len(messages) == 1
        assert "SOREN_CHROME_CROP_Y=40" in messages[0] and "100" in messages[0]

    @pytest.mark.parametrize(
        ("file_text", "env"),
        [
            ("config:\n  chrome_height_px: 100\n", "100"),  # same value
            (None, "40"),  # nothing to override
            ("config:\n  chrome_height_px: 100\n", None),  # no env
        ],
    )
    def test_no_warning_otherwise(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, file_text: str | None, env: str | None
    ) -> None:
        environ = {"SOREN_CHROME_CROP_Y": env} if env is not None else {}
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            _resolved(tmp_path, file_text=file_text, environ=environ)
        assert caplog.records == []


class TestResolveForTask:
    def _registry(self, tmp_path: Path, files: dict[str, str]) -> ServiceRegistry:
        d = tmp_path / "services"
        d.mkdir(exist_ok=True)
        for name, text in files.items():
            (d / name).write_text(text, encoding="utf-8")
        return ServiceRegistry(d)

    def test_uses_matching_persona_source_and_service_ref(self, tmp_path: Path) -> None:
        persona = Persona(
            name="P",
            id="p1",
            task_sources=[TaskSource(name="acme-main", service="acmepanel", typing_delay_ms=90)],
        )
        registry = self._registry(tmp_path, {"acmepanel.yaml": "config:\n  chrome_height_px: 100\n"})
        r = resolve_for_task(_task("acme-main"), persona, settings=SorenSettings(), registry=registry, environ=NO_ENV)
        assert r is not None
        assert r.name == "acmepanel"
        assert r.config.chrome_height_px == 100
        assert r.config.typing_delay_ms == 90
        assert r.source_of("typing_delay_ms") == "persona:p1"

    def test_falls_back_to_task_source_name(self, tmp_path: Path) -> None:
        registry = self._registry(tmp_path, {"acmepanel.yaml": "config:\n  chrome_height_px: 100\n"})
        r = resolve_for_task(
            _task("acmepanel"), Persona(name="P"), settings=SorenSettings(), registry=registry, environ=NO_ENV
        )
        assert r is not None and r.config.chrome_height_px == 100

    def test_no_source_name_means_no_service(self, tmp_path: Path) -> None:
        r = resolve_for_task(
            _task(""),
            Persona(name="P"),
            settings=SorenSettings(),
            registry=self._registry(tmp_path, {}),
            environ=NO_ENV,
        )
        assert r is None

    def test_invalid_file_propagates(self, tmp_path: Path) -> None:
        registry = self._registry(tmp_path, {"acmepanel.yaml": "config:\n  step_budget: 0\n"})
        with pytest.raises(ServiceConfigError):
            resolve_for_task(_task(), Persona(name="P"), settings=SorenSettings(), registry=registry, environ=NO_ENV)

    def test_mock_settings_treated_as_no_overrides(self, tmp_path: Path) -> None:
        """Many existing tests pass MagicMock settings; resolution must not choke on them."""
        registry = self._registry(tmp_path, {"acmepanel.yaml": "config:\n  enter_key_behavior: escape_enter\n"})
        r = resolve_for_task(_task(), Persona(name="P"), settings=MagicMock(), registry=registry, environ=NO_ENV)
        assert r is not None
        assert r.config.enter_key_behavior == "escape_enter"
        assert r.config.step_budget == 50
