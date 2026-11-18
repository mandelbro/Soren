"""`train run` target resolution: URL precedence, persona/service matching, and service config (ADR-0002)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from soren.cli import cli


class TestUrlResolvedFromPersona:
    """The CLI resolves the training target (URL, service config, source name)
    from --url, persona task_sources, and config/services (ADR-0002) so the
    operator doesn't have to retype the URL or export SOREN_CHROME_CROP_Y."""

    @staticmethod
    def _persona_with_source(source_name: str, url: str, persona_id: str = "test", **source_kwargs):
        """Build a minimal Persona object with one task_source."""
        from soren.models.persona import Persona
        from soren.models.task_source import TaskSource

        return Persona(
            name="Test Persona",
            id=persona_id,
            description="test",
            task_sources=[TaskSource(name=source_name, url=url, credential_ref="x", **source_kwargs)],
        )

    @staticmethod
    def _target(source: str, personas, tmp_path, url_flag=None, persona_id=None, files=None):
        from soren.cli.training_target import resolve_training_target
        from soren.services.config_loader import SorenSettings
        from soren.services.service_registry import ServiceRegistry
        from tests.service_helpers import write_service

        services_dir = tmp_path / "services"
        for name, fields in (files or {}).items():
            write_service(services_dir, name, **fields)
        return resolve_training_target(
            source,
            url_flag=url_flag,
            personas=personas,
            persona_id=persona_id,
            settings=SorenSettings(),
            registry=ServiceRegistry(services_dir),
            environ={},
        )

    def test_persona_url_used_when_flag_absent(self, tmp_path):
        personas = [self._persona_with_source("acmepanel", "https://acmepanel.example/home")]
        target = self._target("acmepanel", personas, tmp_path)
        assert target.url == "https://acmepanel.example/home"
        assert target.url_origin == "persona:test"

    def test_explicit_url_flag_overrides_persona(self, tmp_path):
        personas = [self._persona_with_source("acmepanel", "https://acmepanel.example/home")]
        target = self._target("acmepanel", personas, tmp_path, url_flag="https://override.example.com")
        assert target.url == "https://override.example.com"
        assert target.url_origin == "--url flag"

    def test_no_url_when_source_not_in_any_persona(self, tmp_path):
        personas = [self._persona_with_source("other-source", "https://other.example.com")]
        target = self._target("acmepanel", personas, tmp_path)
        assert target.url == ""
        assert target.persona is None

    def test_first_matching_persona_wins(self, tmp_path):
        personas = [
            self._persona_with_source("acmepanel", "https://first.example.com", persona_id="one"),
            self._persona_with_source("acmepanel", "https://second.example.com", persona_id="two"),
        ]
        target = self._target("acmepanel", personas, tmp_path)
        assert target.url == "https://first.example.com"
        assert target.persona is not None and target.persona.id == "one"

    def test_empty_persona_url_skipped(self, tmp_path):
        personas = [
            self._persona_with_source("acmepanel", "", persona_id="one"),  # matches name but no URL
            self._persona_with_source("acmepanel", "https://has-url.example.com", persona_id="two"),
        ]
        target = self._target("acmepanel", personas, tmp_path)
        assert target.url == "https://has-url.example.com"

    def test_service_file_url_used_without_persona(self, tmp_path):
        target = self._target("gcal", [], tmp_path, files={"gcal": {"url": "https://calendar.example"}})
        assert target.url == "https://calendar.example"
        assert target.url_origin.startswith("service_file:")
        assert target.source_name == "gcal"

    def test_persona_explicit_url_beats_service_file(self, tmp_path):
        personas = [self._persona_with_source("gcal", "https://persona.example")]
        target = self._target("gcal", personas, tmp_path, files={"gcal": {"url": "https://file.example"}})
        assert target.url == "https://persona.example"

    def test_binding_form_matched_by_service_name(self, tmp_path):
        """`train run acmepanel` finds a source named acme-main that binds service acmepanel.
        The playbook key is the source name, which is what execution looks up."""
        personas = [self._persona_with_source("acme-main", "", service="acmepanel")]
        target = self._target("acmepanel", personas, tmp_path, files={"acmepanel": {"url": "https://acme.example"}})
        assert target.source_name == "acme-main"
        assert target.service.name == "acmepanel"
        assert target.url == "https://acme.example"

    def test_persona_option_selects_that_persona(self, tmp_path):
        personas = [
            self._persona_with_source("acmepanel", "https://first.example.com", persona_id="one"),
            self._persona_with_source("acmepanel", "https://second.example.com", persona_id="two"),
        ]
        target = self._target("acmepanel", personas, tmp_path, persona_id="two")
        assert target.url == "https://second.example.com"

    def test_persona_option_unknown_raises(self, tmp_path):
        with pytest.raises(click.ClickException, match="nobody"):
            self._target("acmepanel", [], tmp_path, persona_id="nobody")

    def test_persona_option_without_matching_source_raises(self, tmp_path):
        personas = [self._persona_with_source("other", "https://o.example", persona_id="one")]
        with pytest.raises(click.ClickException, match="acmepanel"):
            self._target("acmepanel", personas, tmp_path, persona_id="one")

    def test_later_candidates_not_resolved_once_a_url_is_found(self, tmp_path):
        """Only the service actually used is resolved: a broken file referenced by a
        later persona must not block training with an earlier, working one."""
        personas = [
            self._persona_with_source("acmepanel", "https://ok.example", persona_id="one"),
            self._persona_with_source("acmepanel", "", persona_id="two", service="broken"),
        ]
        (tmp_path / "services").mkdir()
        (tmp_path / "services" / "broken.yaml").write_text("config:\n  step_budget: 0\n", encoding="utf-8")
        target = self._target("acmepanel", personas, tmp_path)
        assert target.url == "https://ok.example"

    def test_service_config_carried_on_target(self, tmp_path):
        target = self._target("gcal", [], tmp_path, files={"gcal": {"config": {"chrome_height_px": 100}}})
        assert target.service.config.chrome_height_px == 100


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
            "acmepanel", "https://from-persona.example.com"
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
                ["train", "run", "acmepanel", "--flow", "login", "--url", "https://acmepanel.example"],
            )

        assert result.exit_code == 0, result.output
        assert MockSession.called, "TrainingSession should be instantiated"
        ctor_kwargs = MockSession.call_args.kwargs
        assert ctor_kwargs.get("source_url") == "https://acmepanel.example", (
            f"Expected source_url='https://acmepanel.example' (flag wins); got {ctor_kwargs.get('source_url')!r}"
        )

    def test_url_from_persona_when_flag_absent(self, runner):
        """When --url is omitted, the CLI looks up the URL from the persona
        task_sources list by source name."""
        MockSession = self._patched_session_cls()
        persona = TestUrlResolvedFromPersona._persona_with_source("acmepanel", "https://acmepanel.example/home")
        with (
            patch("soren.cli.train._configure_training_logging"),
            patch("soren.cli.train.TrainingSession", MockSession),
            patch("soren.cli.train.load_settings"),
            patch("soren.cli.train.LLMService"),
            patch("soren.cli.train.VMManager"),
            patch("soren.cli.train.PlaybookService"),
            patch("soren.cli.train.load_all", return_value=[persona]),
        ):
            result = runner.invoke(cli, ["train", "run", "acmepanel", "--flow", "login"])

        assert result.exit_code == 0, result.output
        assert MockSession.called, "TrainingSession should be instantiated"
        ctor_kwargs = MockSession.call_args.kwargs
        assert ctor_kwargs.get("source_url") == "https://acmepanel.example/home", (
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
            result = runner.invoke(cli, ["train", "run", "acmepanel", "--flow", "login"])

        assert result.exit_code == 0, result.output
        assert MockSession.called, "TrainingSession should be instantiated"
        ctor_kwargs = MockSession.call_args.kwargs
        assert ctor_kwargs.get("source_url") == "", (
            f"Expected source_url='' (no match); got {ctor_kwargs.get('source_url')!r}"
        )


class TestTrainRunServiceConfig:
    """`train run` resolves the service config and hands it to TrainingSession (ADR-0002)."""

    @staticmethod
    def _patches(MockSession, personas=None, vm_cls=None):
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(patch("soren.cli.train._configure_training_logging"))
        stack.enter_context(patch("soren.cli.train.TrainingSession", MockSession))
        stack.enter_context(patch("soren.cli.train.load_settings"))
        stack.enter_context(patch("soren.cli.train.LLMService"))
        stack.enter_context(patch("soren.cli.train.VMManager", vm_cls or MagicMock()))
        stack.enter_context(patch("soren.cli.train.PlaybookService"))
        stack.enter_context(patch("soren.cli.train.load_all", return_value=personas or []))
        return stack

    def test_service_passed_to_session_and_summarized(self, services_dir):
        from tests.service_helpers import write_service

        write_service(services_dir, "gcal", url="https://calendar.example", config={"chrome_height_px": 100})
        MockSession = TestTrainRunUrlFlag._patched_session_cls()
        runner = CliRunner(env={"COLUMNS": "250", "SOREN_CHROME_CROP_Y": None})
        with self._patches(MockSession):
            result = runner.invoke(cli, ["train", "run", "gcal", "--flow", "login"])
        assert result.exit_code == 0, result.output
        kwargs = MockSession.call_args.kwargs
        assert kwargs["service"].config.chrome_height_px == 100
        assert kwargs["source_url"] == "https://calendar.example"
        assert "Chrome crop: 100px" in result.output
        assert "service_file:" in result.output

    def test_invalid_service_file_exits_before_vm(self, services_dir):
        (services_dir / "gcal.yaml").write_text("config:\n  step_budget: 0\n", encoding="utf-8")
        MockSession = TestTrainRunUrlFlag._patched_session_cls()
        vm_cls = MagicMock()
        with self._patches(MockSession, vm_cls=vm_cls):
            result = CliRunner().invoke(cli, ["train", "run", "gcal", "--flow", "login"])
        assert result.exit_code == 1
        assert "gcal.yaml" in result.output
        MockSession.assert_not_called()
        vm_cls.assert_not_called()

    def test_unknown_persona_exits_1(self):
        MockSession = TestTrainRunUrlFlag._patched_session_cls()
        with self._patches(MockSession):
            result = CliRunner().invoke(cli, ["train", "run", "gcal", "--persona", "nobody"])
        assert result.exit_code == 1
        assert "nobody" in result.output
        MockSession.assert_not_called()

    def test_binding_form_source_name_used_for_playbooks(self, services_dir):
        from tests.service_helpers import write_service

        write_service(services_dir, "acmepanel", url="https://acme.example")
        persona = TestUrlResolvedFromPersona._persona_with_source("acme-main", "", service="acmepanel")
        MockSession = TestTrainRunUrlFlag._patched_session_cls()
        with self._patches(MockSession, personas=[persona]):
            result = CliRunner().invoke(cli, ["train", "run", "acmepanel", "--flow", "login"])
        assert result.exit_code == 0, result.output
        assert MockSession.call_args.kwargs["source_name"] == "acme-main"
        assert MockSession.call_args.kwargs["source_url"] == "https://acme.example"
