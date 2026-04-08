"""Tests for the `soren train run` CLI command (Task 016)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from soren.cli import cli
from soren.cli.train import _SILENCED_NOISY_LOGGERS, _TRAINING_LOGGERS, _configure_training_logging


@pytest.fixture
def runner():
    return CliRunner()


def _mock_settings():
    """Return a minimal mock SorenSettings."""
    settings = MagicMock()
    settings.playbooks.base_dir = "data/playbooks"
    settings.playbooks.site_playbooks_dir = "data/playbooks/site"
    settings.playbooks.host_playbooks_dir = "data/playbooks/host"
    settings.playbooks.base_playbooks_dir = "data/playbooks/base"
    settings.vm = MagicMock()
    settings.llm = MagicMock()
    return settings


class TestTrainGroupRegistered:
    def test_train_group_exists(self, runner):
        result = runner.invoke(cli, ["train", "--help"])
        assert result.exit_code == 0
        assert "Training" in result.output or "train" in result.output

    def test_train_run_listed(self, runner):
        result = runner.invoke(cli, ["train", "--help"])
        assert result.exit_code == 0
        assert "run" in result.output


class TestTrainRunCommand:
    def test_train_run_requires_source(self, runner):
        result = runner.invoke(cli, ["train", "run"])
        assert result.exit_code != 0
        assert "Missing argument" in result.output or "source" in result.output.lower()

    def test_train_run_invokes_session(self, runner, tmp_path):
        """train run without --flow trains all three flows sequentially."""
        from soren.models.site_playbook import FlowPlaybook, PlaybookStep

        mock_flow = FlowPlaybook(
            steps=[PlaybookStep(seq=1, instruction="Click login", expected_after="Logged in")],
            confidence=0.5,
            verified_by="human",
        )

        with (
            patch("soren.cli.train.load_settings", return_value=_mock_settings()),
            patch("soren.cli.train.LLMService"),
            patch("soren.cli.train.VMManager") as mock_vm_cls,
            patch("soren.cli.train.PlaybookService"),
            patch("soren.cli.train.TrainingSession") as mock_session_cls,
        ):
            mock_vm = MagicMock()
            mock_vm_cls.return_value = mock_vm

            mock_session = MagicMock()
            mock_session.run = AsyncMock(return_value=mock_flow)
            mock_session.steps = [PlaybookStep(seq=1, instruction="Click login", expected_after="Logged in")]
            mock_session_cls.return_value = mock_session

            result = runner.invoke(cli, ["train", "run", "prolific"])

        assert result.exit_code == 0
        # Without --flow, trains login -> discovery -> execution (3 calls)
        assert mock_session_cls.call_count == 3
        first_call = mock_session_cls.call_args_list[0]
        assert first_call.kwargs["source_name"] == "prolific"
        assert first_call.kwargs["flow_type"] == "login"

    def test_train_run_with_flow_option(self, runner):
        """--flow option is passed to TrainingSession."""
        from soren.models.site_playbook import FlowPlaybook

        mock_flow = FlowPlaybook(steps=[], confidence=0.5, verified_by="human")

        with (
            patch("soren.cli.train.load_settings", return_value=_mock_settings()),
            patch("soren.cli.train.LLMService"),
            patch("soren.cli.train.VMManager"),
            patch("soren.cli.train.PlaybookService"),
            patch("soren.cli.train.TrainingSession") as mock_session_cls,
        ):
            mock_session = MagicMock()
            mock_session.run = AsyncMock(return_value=mock_flow)
            mock_session.steps = []
            mock_session_cls.return_value = mock_session

            result = runner.invoke(cli, ["train", "run", "prolific", "--flow", "discovery"])

        assert result.exit_code == 0
        call_kwargs = mock_session_cls.call_args
        assert call_kwargs.kwargs["flow_type"] == "discovery"

    def test_train_run_abort_shows_message(self, runner):
        """When session returns None (abort), show abort message."""
        with (
            patch("soren.cli.train.load_settings", return_value=_mock_settings()),
            patch("soren.cli.train.LLMService"),
            patch("soren.cli.train.VMManager"),
            patch("soren.cli.train.PlaybookService"),
            patch("soren.cli.train.TrainingSession") as mock_session_cls,
        ):
            mock_session = MagicMock()
            mock_session.run = AsyncMock(return_value=None)
            mock_session.steps = []
            mock_session_cls.return_value = mock_session

            result = runner.invoke(cli, ["train", "run", "prolific"])

        assert result.exit_code == 0
        assert "abort" in result.output.lower() or "cancel" in result.output.lower()

    def test_train_run_all_flows_sequential(self, runner):
        """Without --flow, trains all three flows sequentially."""
        from soren.models.site_playbook import FlowPlaybook

        mock_flow = FlowPlaybook(steps=[], confidence=0.5, verified_by="human")

        with (
            patch("soren.cli.train.load_settings", return_value=_mock_settings()),
            patch("soren.cli.train.LLMService"),
            patch("soren.cli.train.VMManager"),
            patch("soren.cli.train.PlaybookService"),
            patch("soren.cli.train.TrainingSession") as mock_session_cls,
        ):
            mock_session = MagicMock()
            mock_session.run = AsyncMock(return_value=mock_flow)
            mock_session.steps = []
            mock_session_cls.return_value = mock_session

            result = runner.invoke(cli, ["train", "run", "prolific"])

        assert result.exit_code == 0
        # Should have created 3 sessions (login, discovery, execution)
        assert mock_session_cls.call_count == 3
        flow_types = [c.kwargs["flow_type"] for c in mock_session_cls.call_args_list]
        assert flow_types == ["login", "discovery", "execution"]


class TestRichTrainingCallbacks:
    def test_callbacks_class_importable(self):
        from soren.cli.train import RichTrainingCallbacks

        cb = RichTrainingCallbacks()
        assert hasattr(cb, "show_screenshot")
        assert hasattr(cb, "get_instruction")
        assert hasattr(cb, "confirm_step")
        assert hasattr(cb, "get_expected_after")
        assert hasattr(cb, "show_message")
        assert hasattr(cb, "show_error")


class TestConfigureTrainingLogging:
    """Without this configuration, click coordinates and VNC scale factor stay invisible."""

    def _reset_loggers(self):
        for name in _TRAINING_LOGGERS:
            lg = logging.getLogger(name)
            lg.handlers = [h for h in lg.handlers if not getattr(h, "_soren_training_handler", False)]
            lg.setLevel(logging.NOTSET)
            lg.propagate = True
        for name in _SILENCED_NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.NOTSET)

    def test_default_promotes_loggers_to_info(self):
        self._reset_loggers()
        try:
            _configure_training_logging(debug=False)
            for name in _TRAINING_LOGGERS:
                assert logging.getLogger(name).level == logging.INFO, f"{name} not at INFO"
        finally:
            self._reset_loggers()

    def test_debug_flag_promotes_loggers_to_debug(self):
        self._reset_loggers()
        try:
            _configure_training_logging(debug=True)
            for name in _TRAINING_LOGGERS:
                assert logging.getLogger(name).level == logging.DEBUG, f"{name} not at DEBUG"
        finally:
            self._reset_loggers()

    def test_idempotent_does_not_attach_duplicate_handlers(self):
        self._reset_loggers()
        try:
            _configure_training_logging(debug=False)
            _configure_training_logging(debug=False)
            _configure_training_logging(debug=True)
            for name in _TRAINING_LOGGERS:
                lg = logging.getLogger(name)
                marked = [h for h in lg.handlers if getattr(h, "_soren_training_handler", False)]
                assert len(marked) == 1, f"{name} has {len(marked)} training handlers, expected 1"
        finally:
            self._reset_loggers()

    def test_noisy_third_party_loggers_raised_to_error(self):
        """core.telemetry.{sentry,otel} WARNING spam must be silenced during training."""
        self._reset_loggers()
        try:
            _configure_training_logging(debug=False)
            for name in _SILENCED_NOISY_LOGGERS:
                assert logging.getLogger(name).level == logging.ERROR, f"{name} should be at ERROR"
        finally:
            self._reset_loggers()

    def test_run_command_accepts_debug_flag(self, runner):
        with patch("soren.cli.train._configure_training_logging") as mock_cfg:
            # Close the inner coroutine immediately so asyncio.run doesn't get
            # an un-awaited coroutine warning when we mock it out.
            def _consume_coro(coro):
                coro.close()

            with patch("soren.cli.train.asyncio.run", side_effect=_consume_coro):
                result = runner.invoke(cli, ["train", "run", "test-source", "--debug"])
            assert result.exit_code == 0, result.output
            mock_cfg.assert_called_once_with(debug=True)


class TestUrlResolvedFromPersona:
    """The CLI looks up the task source URL from persona task_sources when
    --url is not explicitly provided, so the operator doesn't have to retype
    it every training run. Persona files already carry the URL — this is
    pure plumbing."""

    @staticmethod
    def _persona_with_source(source_name: str, url: str):
        """Build a minimal Persona object with one task_source."""
        from soren.models.persona import Persona
        from soren.models.task_source import TaskSource

        return Persona(
            name="Test Persona",
            id="test",
            description="test",
            task_sources=[TaskSource(name=source_name, url=url, credential_ref="x")],
        )

    def test_persona_url_used_when_flag_absent(self, runner):
        """When --url is not passed, the CLI finds the source by name in
        the persona task_sources list and uses that URL."""
        from soren.cli.train import _resolve_source_url

        personas = [self._persona_with_source("sproutful", "https://sproutful.com/home")]
        resolved = _resolve_source_url(source_name="sproutful", url_flag=None, personas=personas)
        assert resolved == "https://sproutful.com/home"

    def test_explicit_url_flag_overrides_persona(self, runner):
        """When --url IS passed, it takes precedence over the persona URL."""
        from soren.cli.train import _resolve_source_url

        personas = [self._persona_with_source("sproutful", "https://sproutful.com/home")]
        resolved = _resolve_source_url(
            source_name="sproutful",
            url_flag="https://override.example.com",
            personas=personas,
        )
        assert resolved == "https://override.example.com"

    def test_no_url_when_source_not_in_any_persona(self, runner):
        """If the source name doesn't match any persona task_source, return
        empty string — the training session will skip auto-navigation."""
        from soren.cli.train import _resolve_source_url

        personas = [self._persona_with_source("other-source", "https://other.example.com")]
        resolved = _resolve_source_url(source_name="sproutful", url_flag=None, personas=personas)
        assert resolved == ""

    def test_first_matching_persona_wins(self, runner):
        """When multiple personas list the same source, the first match with
        a non-empty URL is used. Personas are scanned in load order."""
        from soren.cli.train import _resolve_source_url

        personas = [
            self._persona_with_source("sproutful", "https://first.example.com"),
            self._persona_with_source("sproutful", "https://second.example.com"),
        ]
        resolved = _resolve_source_url(source_name="sproutful", url_flag=None, personas=personas)
        assert resolved == "https://first.example.com"

    def test_empty_persona_url_skipped(self, runner):
        """A persona with a matching source name but url='' is skipped;
        if another persona has a non-empty URL for the same source, that's used."""
        from soren.cli.train import _resolve_source_url

        personas = [
            self._persona_with_source("sproutful", ""),  # matches name but no URL
            self._persona_with_source("sproutful", "https://has-url.example.com"),
        ]
        resolved = _resolve_source_url(source_name="sproutful", url_flag=None, personas=personas)
        assert resolved == "https://has-url.example.com"


class TestTrainRunUrlFlag:
    """--url flag threads into TrainingSession.source_url so _navigate can
    auto-dispatch Cmd+L + type URL + Return via the keyboard path (ADR-0001).
    """

    @staticmethod
    def _patched_session_cls():
        """Return a MagicMock suitable for patching TrainingSession that
        yields instances whose .run() returns None asynchronously (simulating
        an aborted training session so the CLI's ``if result is None`` branch
        short-circuits before touching downstream code)."""
        mock_instance = MagicMock()
        mock_instance.run = AsyncMock(return_value=None)
        cls = MagicMock(return_value=mock_instance)
        return cls

    def test_url_flag_threaded_to_session(self, runner):
        """Running `train run SOURCE --url https://example.com` must construct
        TrainingSession with source_url set to that URL, even if a persona
        has a different URL configured (flag wins over persona)."""
        MockSession = self._patched_session_cls()
        persona_with_different_url = TestUrlResolvedFromPersona._persona_with_source(
            "sproutful", "https://from-persona.example.com"
        )
        with (
            patch("soren.cli.train._configure_training_logging"),
            patch("soren.cli.train.TrainingSession", MockSession),
            patch("soren.cli.train.load_settings"),
            patch("soren.cli.train.LLMService"),
            patch("soren.cli.train.VMManager"),
            patch("soren.cli.train.PlaybookService"),
            patch("soren.cli.train.load_all", return_value=[persona_with_different_url]),
        ):
            result = runner.invoke(
                cli,
                ["train", "run", "sproutful", "--flow", "login", "--url", "https://sproutful.com"],
            )

        assert result.exit_code == 0, result.output
        assert MockSession.called, "TrainingSession should be instantiated"
        ctor_kwargs = MockSession.call_args.kwargs
        assert ctor_kwargs.get("source_url") == "https://sproutful.com", (
            f"Expected source_url='https://sproutful.com' (flag wins); got {ctor_kwargs.get('source_url')!r}"
        )

    def test_url_from_persona_when_flag_absent(self, runner):
        """When --url is omitted, the CLI looks up the URL from the persona
        task_sources list by source name."""
        MockSession = self._patched_session_cls()
        persona = TestUrlResolvedFromPersona._persona_with_source("sproutful", "https://sproutful.com/home")
        with (
            patch("soren.cli.train._configure_training_logging"),
            patch("soren.cli.train.TrainingSession", MockSession),
            patch("soren.cli.train.load_settings"),
            patch("soren.cli.train.LLMService"),
            patch("soren.cli.train.VMManager"),
            patch("soren.cli.train.PlaybookService"),
            patch("soren.cli.train.load_all", return_value=[persona]),
        ):
            result = runner.invoke(cli, ["train", "run", "sproutful", "--flow", "login"])

        assert result.exit_code == 0, result.output
        assert MockSession.called, "TrainingSession should be instantiated"
        ctor_kwargs = MockSession.call_args.kwargs
        assert ctor_kwargs.get("source_url") == "https://sproutful.com/home", (
            f"Expected source_url from persona; got {ctor_kwargs.get('source_url')!r}"
        )

    def test_source_url_empty_when_no_persona_match(self, runner):
        """If neither --url nor any persona provides a URL, source_url is ''
        and TrainingSession will skip auto-navigation."""
        MockSession = self._patched_session_cls()
        other_persona = TestUrlResolvedFromPersona._persona_with_source("other-source", "https://other.example.com")
        with (
            patch("soren.cli.train._configure_training_logging"),
            patch("soren.cli.train.TrainingSession", MockSession),
            patch("soren.cli.train.load_settings"),
            patch("soren.cli.train.LLMService"),
            patch("soren.cli.train.VMManager"),
            patch("soren.cli.train.PlaybookService"),
            patch("soren.cli.train.load_all", return_value=[other_persona]),
        ):
            result = runner.invoke(cli, ["train", "run", "sproutful", "--flow", "login"])

        assert result.exit_code == 0, result.output
        assert MockSession.called, "TrainingSession should be instantiated"
        ctor_kwargs = MockSession.call_args.kwargs
        assert ctor_kwargs.get("source_url") == "", (
            f"Expected source_url='' (no match); got {ctor_kwargs.get('source_url')!r}"
        )
