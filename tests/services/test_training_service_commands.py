"""TrainingServiceCommands: /shortcut /instruction /note /crop /service during training (Phase 4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from soren.models.service import ServiceConfig
from soren.models.task_source import TaskSource
from soren.services.config_loader import SorenSettings
from soren.services.service_registry import ServiceRegistry
from soren.services.service_resolution import resolve_service
from soren.services.service_writer import ServiceWriter
from soren.services.training_service_commands import TrainingServiceCommands
from tests.service_helpers import make_resolved_service, write_service


@pytest.fixture
def svc(tmp_path: Path):
    return make_resolved_service(tmp_path, "gcal", url="https://calendar.example", config={"chrome_height_px": 100})


def _registry(tmp_path: Path) -> ServiceRegistry:
    return ServiceRegistry(tmp_path / "services")


class TestDispatch:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("/shortcut w Week", True),
            ("/SHORTCUT w Week", True),
            ("/instruction x", True),
            ("/note x", True),
            ("/crop 90", True),
            ("/service", True),
            ("/done", False),
            ("/shortcuts", False),
            ("Click the button", False),
        ],
    )
    def test_handles(self, svc, text: str, expected: bool) -> None:
        assert TrainingServiceCommands(svc).handles(text) is expected

    def test_unavailable_without_service(self) -> None:
        commands = TrainingServiceCommands(None)
        assert "unavailable" in commands.handle("/crop 90")
        assert commands.pending.is_empty


class TestShortcut:
    def test_with_when(self, svc) -> None:
        commands = TrainingServiceCommands(svc)
        msg = commands.handle("/shortcut w Switch to week view | no text field is focused")
        [shortcut] = commands.pending.shortcuts
        assert (shortcut.keys, shortcut.action, shortcut.when) == (
            ["w"],
            "Switch to week view",
            "no text field is focused",
        )
        assert "hotkey(key='w')" in msg

    def test_combo_keys(self, svc) -> None:
        commands = TrainingServiceCommands(svc)
        commands.handle("/shortcut cmd+k Open quick search")
        assert commands.pending.shortcuts[0].keys == ["cmd", "k"]

    @pytest.mark.parametrize("text", ["/shortcut", "/shortcut w", "/shortcut w | when only"])
    def test_missing_action_shows_usage(self, svc, text: str) -> None:
        commands = TrainingServiceCommands(svc)
        assert "Usage: /shortcut" in commands.handle(text)
        assert commands.pending.is_empty

    def test_malformed_keys_rejected(self, svc) -> None:
        commands = TrainingServiceCommands(svc)
        assert "Invalid shortcut" in commands.handle("/shortcut cmd++k Open")
        assert commands.pending.is_empty


class TestTextCommands:
    def test_instruction(self, svc) -> None:
        commands = TrainingServiceCommands(svc)
        commands.handle("/instruction The Create button is top-left")
        assert commands.pending.instructions == ["The Create button is top-left"]

    def test_note_keeps_case_and_punctuation(self, svc) -> None:
        commands = TrainingServiceCommands(svc)
        commands.handle("/note Login opens a MODAL: email first.")
        assert commands.pending.notes == ["Login opens a MODAL: email first."]

    @pytest.mark.parametrize("text", ["/instruction", "/note   "])
    def test_empty_text_shows_usage(self, svc, text: str) -> None:
        commands = TrainingServiceCommands(svc)
        assert "Usage:" in commands.handle(text)
        assert commands.pending.is_empty


class TestCrop:
    def test_staged_and_applied_live(self, svc) -> None:
        commands = TrainingServiceCommands(svc)
        msg = commands.handle("/crop 90")
        assert commands.pending.chrome_height_px == 90
        assert commands.service is not None
        assert commands.service.config.chrome_height_px == 90
        assert commands.service.source_of("chrome_height_px") == "training:/crop"
        assert svc.config.chrome_height_px == 100  # original resolved service not mutated
        assert "90px" in msg

    @pytest.mark.parametrize("text", ["/crop abc", "/crop -5", "/crop 1.5", "/crop 501"])
    def test_invalid_values(self, svc, text: str) -> None:
        commands = TrainingServiceCommands(svc)
        assert "Usage: /crop" in commands.handle(text)
        assert commands.pending.is_empty
        assert commands.service is svc

    def test_no_argument_shows_current(self, svc) -> None:
        msg = TrainingServiceCommands(svc).handle("/crop")
        assert "100px" in msg and "service_file:" in msg


class TestServiceCommand:
    def test_shows_pending_changes(self, svc) -> None:
        commands = TrainingServiceCommands(svc)
        assert "No pending service changes" in commands.handle("/service")
        commands.handle("/instruction Tip")
        assert "config.instructions += Tip" in commands.handle("/service")

    def test_discard(self, svc) -> None:
        commands = TrainingServiceCommands(svc)
        commands.handle("/crop 90")
        assert "Discarded" in commands.handle("/service discard")
        assert commands.pending.is_empty
        assert commands.service is svc  # live crop reverted too

    def test_unknown_subcommand(self, svc) -> None:
        assert "Usage: /service" in TrainingServiceCommands(svc).handle("/service frobnicate")


class TestPrepareSave:
    def _plan(self, commands: TrainingServiceCommands, tmp_path: Path, source_url: str = ""):
        registry = _registry(tmp_path)
        return commands.prepare_save(registry=registry, writer=ServiceWriter(registry), source_url=source_url)

    def test_nothing_to_save(self, svc, tmp_path: Path) -> None:
        assert self._plan(TrainingServiceCommands(svc), tmp_path) is None

    def test_plan_applies_pending_to_existing_file(self, svc, tmp_path: Path) -> None:
        commands = TrainingServiceCommands(svc)
        commands.handle("/instruction Tip")
        plan = self._plan(commands, tmp_path)
        assert plan is not None
        assert plan.path == tmp_path / "services" / "gcal.yaml"
        assert plan.service.config.instructions == ["Tip"]
        assert plan.service.config.chrome_height_px == 100  # existing file value kept
        assert plan.changes == ["config.instructions += Tip"]
        assert plan.warnings == []

    def test_auto_url_proposed_when_file_has_none(self, tmp_path: Path) -> None:
        svc = make_resolved_service(tmp_path, "gcal")
        plan = self._plan(TrainingServiceCommands(svc), tmp_path, source_url="https://cal.example")
        assert plan is not None and plan.service.url == "https://cal.example"
        assert plan.changes == ["url: https://cal.example (auto: training navigation URL)"]

    def test_auto_url_not_proposed_when_file_has_url(self, svc, tmp_path: Path) -> None:
        assert self._plan(TrainingServiceCommands(svc), tmp_path, source_url="https://other.example") is None

    def test_auto_crop_from_env_proposed_when_file_has_no_crop(self, tmp_path: Path) -> None:
        """The env var was the only crop that worked during training: offer to make it permanent."""
        svc = make_resolved_service(tmp_path, "gcal", environ={"SOREN_CHROME_CROP_Y": "100"})
        plan = self._plan(TrainingServiceCommands(svc), tmp_path)
        assert plan is not None and plan.service.config.chrome_height_px == 100
        assert plan.changes == ["config.chrome_height_px: 100 (auto: SOREN_CHROME_CROP_Y)"]
        assert plan.warnings == []  # env value == saved value, nothing shadowed

    def test_env_crop_never_replaces_the_file_value(self, tmp_path: Path) -> None:
        """A stale SOREN_CHROME_CROP_Y must stay a debug override, not be saved over a configured crop."""
        svc = make_resolved_service(
            tmp_path, "gcal", config={"chrome_height_px": 80}, environ={"SOREN_CHROME_CROP_Y": "100"}
        )
        commands = TrainingServiceCommands(svc)
        commands.handle("/instruction Use the sidebar")
        plan = self._plan(commands, tmp_path)
        assert plan is not None
        assert plan.service.config.chrome_height_px == 80
        assert plan.changes == ["config.instructions += Use the sidebar"]

    def test_env_crop_alone_does_not_trigger_a_save_when_file_has_a_crop(self, tmp_path: Path) -> None:
        svc = make_resolved_service(
            tmp_path, "gcal", config={"chrome_height_px": 80}, environ={"SOREN_CHROME_CROP_Y": "100"}
        )
        assert self._plan(TrainingServiceCommands(svc), tmp_path) is None

    def test_explicit_crop_beats_auto_crop(self, tmp_path: Path) -> None:
        svc = make_resolved_service(tmp_path, "gcal", environ={"SOREN_CHROME_CROP_Y": "100"})
        commands = TrainingServiceCommands(svc)
        commands.handle("/crop 90")
        plan = self._plan(commands, tmp_path)
        assert plan is not None and plan.service.config.chrome_height_px == 90
        assert any("env:SOREN_CHROME_CROP_Y" in w for w in plan.warnings)

    def test_new_file_seeded_from_non_default_values(self, tmp_path: Path) -> None:
        """Legacy persona values seed a new file (migration aid); defaults are not written."""
        source = TaskSource(name="legacy", url="https://legacy.example", type="generic_form")
        svc = resolve_service(
            "legacy", registry=_registry(tmp_path), settings=SorenSettings(), source=source, environ={}
        )
        commands = TrainingServiceCommands(svc)
        commands.handle("/crop 90")
        plan = self._plan(commands, tmp_path)
        assert plan is not None
        assert (plan.service.url, plan.service.type) == ("https://legacy.example", "generic_form")
        assert plan.service.config.set_values() == {"chrome_height_px": 90}

    def test_comment_loss_warning(self, svc, tmp_path: Path) -> None:
        path = tmp_path / "services" / "gcal.yaml"
        path.write_text("# keep me\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
        commands = TrainingServiceCommands(svc)
        commands.handle("/note hi")
        plan = self._plan(commands, tmp_path)
        assert plan is not None and any("comments" in w for w in plan.warnings)

    def test_override_warning_for_settings_layer(self, tmp_path: Path) -> None:
        services_dir = tmp_path / "services"
        write_service(services_dir, "gcal", config={"instructions": ["file tip"]})
        settings = SorenSettings.model_validate(
            {"services": {"gcal": ServiceConfig(instructions=["settings tip"], step_budget=5)}}
        )
        svc = resolve_service("gcal", registry=ServiceRegistry(services_dir), settings=settings, environ={})
        commands = TrainingServiceCommands(svc)
        commands.handle("/instruction new tip")
        plan = self._plan(commands, tmp_path)
        assert plan is not None
        [warning] = plan.warnings
        assert "instructions" in warning and "settings.services.gcal" in warning
