"""Tests for the `soren train run` CLI command."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

from soren.cli import cli
from soren.cli.train import _SILENCED_NOISY_LOGGERS, _TRAINING_LOGGERS, _configure_training_logging
from soren.services.config_loader import SorenSettings


def _mock_settings():
    """Default settings (playbook dirs under data/playbooks)."""
    return SorenSettings()


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

            result = runner.invoke(cli, ["train", "run", "betapanel"])

        assert result.exit_code == 0
        # Without --flow, trains login -> discovery -> execution (3 calls)
        assert mock_session_cls.call_count == 3
        first_call = mock_session_cls.call_args_list[0]
        assert first_call.kwargs["source_name"] == "betapanel"
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

            result = runner.invoke(cli, ["train", "run", "betapanel", "--flow", "discovery"])

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

            result = runner.invoke(cli, ["train", "run", "betapanel"])

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

            result = runner.invoke(cli, ["train", "run", "betapanel"])

        assert result.exit_code == 0
        # Should have created 3 sessions (login, discovery, execution)
        assert mock_session_cls.call_count == 3
        flow_types = [c.kwargs["flow_type"] for c in mock_session_cls.call_args_list]
        assert flow_types == ["login", "discovery", "execution"]


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


class TestTrainingLoggersCoverTrainingModules:
    """Every training module that logs must be surfaced by `train run` (otherwise its INFO lines vanish)."""

    def test_all_training_module_loggers_configured(self) -> None:
        import importlib
        import logging as _logging
        import pkgutil

        import soren.services

        training_modules = [
            f"soren.services.{m.name}"
            for m in pkgutil.iter_modules(soren.services.__path__)
            if m.name.startswith("training_")
        ]
        logging_modules = [
            name
            for name in training_modules
            if isinstance(getattr(importlib.import_module(name), "logger", None), _logging.Logger)
        ]
        assert "soren.services.training_step_runner" in logging_modules  # sanity: the scan finds loggers
        missing = sorted(set(logging_modules) - set(_TRAINING_LOGGERS))
        assert not missing, f"add to soren.cli.train._TRAINING_LOGGERS: {missing}"
