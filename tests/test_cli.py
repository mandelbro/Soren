"""Tests for the Soren CLI skeleton."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from soren.cli import cli
from soren.models.vm import VMConfig

SUBCOMMAND_GROUPS = [
    "cycle",
    "discover",
    "tasks",
    "vm",
    "credentials",
    "playbooks",
    "personas",
    "history",
    "uat",
]


@pytest.fixture
def runner():
    return CliRunner()


class TestCLIEntryPoint:
    def test_help_renders(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Soren Desktop Agent Framework" in result.output

    def test_version_flag(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output


class TestSubcommandGroups:
    @pytest.mark.parametrize("group", SUBCOMMAND_GROUPS)
    def test_group_is_registered(self, runner, group):
        result = runner.invoke(cli, [group, "--help"])
        assert result.exit_code == 0

    @pytest.mark.parametrize("group", SUBCOMMAND_GROUPS)
    def test_group_help_renders(self, runner, group):
        result = runner.invoke(cli, [group, "--help"])
        assert result.exit_code == 0
        assert "Usage:" in result.output or "Usage" in result.output


class TestCycleCommands:
    def test_cycle_run_runs(self, runner, tmp_path):
        """cycle run executes (mocked to avoid VM/LLM)."""
        with (
            patch("soren.services.task_backlog.get_default_db_path", return_value=tmp_path / "soren.db"),
            patch("soren.cli.VMManager") as mock_vm_cls,
            patch("soren.services.meta_agent.SorenMetaAgent") as mock_agent_cls,
        ):
            from soren.models.session import CycleReport

            mock_vm = MagicMock()
            mock_vm.vm_name = "test-vm"
            mock_vm.ensure_ready = AsyncMock()
            mock_vm.connect_computer = AsyncMock(return_value=MagicMock())
            mock_vm_cls.return_value = mock_vm

            mock_agent = MagicMock()
            mock_agent.run_cycle = AsyncMock(
                return_value=CycleReport(
                    cycle_id="x",
                    cycle_type="execute",
                    started_at="",
                    completed_at="",
                    vm_name="test-vm",
                )
            )
            mock_agent_cls.return_value = mock_agent

            result = runner.invoke(cli, ["cycle", "run"])
        assert result.exit_code == 0
        assert "Cycle complete" in result.output or "Assessing backlog" in result.output


class TestDiscoverCommands:
    def test_discover_refresh_runs(self, runner, tmp_path):
        with (
            patch("soren.services.task_backlog.get_default_db_path", return_value=tmp_path / "soren.db"),
            patch("soren.cli.VMManager") as mock_vm_cls,
            patch("soren.services.task_discovery.TaskDiscovery") as mock_discovery_cls,
        ):
            mock_vm = AsyncMock()
            mock_vm.ensure_ready = AsyncMock()
            mock_vm.connect_computer = AsyncMock(return_value=MagicMock())
            mock_vm_cls.return_value = mock_vm
            mock_discovery = AsyncMock()
            mock_discovery.discover = AsyncMock(return_value=[])
            mock_discovery_cls.return_value = mock_discovery
            result = runner.invoke(cli, ["discover", "refresh"])
        assert result.exit_code == 0
        assert "Done" in result.output or "Discovering" in result.output or "No personas" in result.output

    def test_discover_refresh_persona_option(self, runner, tmp_path):
        with (
            patch("soren.services.task_backlog.get_default_db_path", return_value=tmp_path / "soren.db"),
            patch("soren.cli.VMManager") as mock_vm_cls,
            patch("soren.services.task_discovery.TaskDiscovery") as mock_discovery_cls,
        ):
            mock_vm = AsyncMock()
            mock_vm.ensure_ready = AsyncMock()
            mock_vm.connect_computer = AsyncMock(return_value=MagicMock())
            mock_vm_cls.return_value = mock_vm
            mock_discovery = AsyncMock()
            mock_discovery.discover = AsyncMock(return_value=[])
            mock_discovery_cls.return_value = mock_discovery
            result = runner.invoke(cli, ["discover", "refresh", "--persona", "researcher"])
        assert result.exit_code == 0


class TestTasksCommands:
    def test_tasks_list_empty_backlog(self, runner, tmp_path):
        with patch("soren.services.task_backlog.get_default_db_path", return_value=tmp_path / "soren.db"):
            result = runner.invoke(cli, ["tasks", "list"])
        assert result.exit_code == 0
        assert "No tasks in backlog" in result.output

    def test_tasks_list_accepts_all_filters(self, runner, tmp_path):
        with patch("soren.services.task_backlog.get_default_db_path", return_value=tmp_path / "soren.db"):
            result = runner.invoke(
                cli,
                [
                    "tasks",
                    "list",
                    "--persona",
                    "dev",
                    "--status",
                    "queued",
                    "--type",
                    "survey",
                    "--limit",
                    "5",
                ],
            )
        assert result.exit_code == 0

    def test_tasks_retry_failed_empty(self, runner, tmp_path):
        with patch("soren.services.task_backlog.get_default_db_path", return_value=tmp_path / "soren.db"):
            result = runner.invoke(cli, ["tasks", "retry-failed"])
        assert result.exit_code == 0
        assert "Requeued 0" in result.output

    def test_tasks_retry_accepts_task_id(self, runner, tmp_path):
        with patch("soren.services.task_backlog.get_default_db_path", return_value=tmp_path / "soren.db"):
            result = runner.invoke(cli, ["tasks", "retry", "abc-123"])
        assert result.exit_code == 0
        assert "Requeued task" in result.output

    def test_tasks_clear_requires_confirmation(self, runner, tmp_path):
        with patch("soren.services.task_backlog.get_default_db_path", return_value=tmp_path / "soren.db"):
            result = runner.invoke(cli, ["tasks", "clear"], input="y\n")
        assert result.exit_code == 0
        assert "Cleared" in result.output


class TestVMCommands:
    def test_vm_prepare_invokes_preparation(self, runner):
        with patch(
            "soren.services.vm_prepare.prepare_pristine_vm",
            new_callable=AsyncMock,
        ) as mock_prepare:
            result = runner.invoke(cli, ["vm", "prepare"])
        assert result.exit_code == 0
        mock_prepare.assert_called_once()

    def test_vm_status_displays_table(self, runner):
        with patch("soren.cli.load_settings") as load:
            load.return_value.vm = VMConfig()
            with patch(
                "soren.services.vm_manager.LumeClient._request",
                new_callable=AsyncMock,
                return_value=[{"name": "soren-vm", "status": "running", "memory": "8GB", "vncUrl": ""}],
            ):
                result = runner.invoke(cli, ["vm", "status"])
        assert result.exit_code == 0
        assert "Lume VMs" in result.output or "soren-vm" in result.output

    def test_vm_stop_requires_confirmation(self, runner):
        with patch("soren.cli.load_settings") as load:
            load.return_value.vm = VMConfig()
            with patch("soren.cli.VMManager") as mock_vm:
                mock_manager = AsyncMock()
                mock_manager.is_running = AsyncMock(return_value=True)
                mock_manager.stop = AsyncMock()
                mock_vm.return_value = mock_manager
                result = runner.invoke(cli, ["vm", "stop"], input="y\n")
        assert result.exit_code == 0
        assert "stopped" in result.output.lower() or mock_manager.stop.called

    def test_vm_reset_displays_results(self, runner):
        with patch("soren.cli.load_settings") as load:
            load.return_value.vm = VMConfig()
            with patch("soren.cli.VMManager") as mock_vm_cls:
                from soren.models.reset import ResetResult

                mock_manager = AsyncMock()
                mock_manager.reset_browser_state = AsyncMock(
                    return_value=ResetResult(
                        success=True,
                        duration_seconds=3.2,
                        window_count=1,
                        warnings=[],
                    )
                )
                mock_vm_cls.return_value = mock_manager
                result = runner.invoke(cli, ["vm", "reset"])
        assert result.exit_code == 0
        assert "Browser reset" in result.output


class TestCredentialsCommands:
    def test_credentials_encrypt_from_plaintext(self, runner, tmp_path):
        plain = tmp_path / "credentials.yaml"
        plain.write_text(
            "credentials:\n"
            "  - ref: sproutful\n"
            "    service_type: survey_platform\n"
            "    username: user@example.com\n"
            "    password: secret\n"
        )
        encrypted = tmp_path / "credentials.enc"
        key_file = tmp_path / "credentials.key"
        result = runner.invoke(
            cli,
            [
                "credentials",
                "encrypt",
                "--input",
                str(plain),
                "--output",
                str(encrypted),
                "--key-file",
                str(key_file),
            ],
        )
        assert result.exit_code == 0
        assert "Encrypted 1 credential record" in result.output

    def test_credentials_add_accepts_args(self, runner, tmp_path):
        encrypted = tmp_path / "credentials.enc"
        key_file = tmp_path / "credentials.key"
        result = runner.invoke(
            cli,
            [
                "credentials",
                "add",
                "my-twitter",
                "twitter",
                "--username",
                "user@example.com",
                "--password",
                "s3cret",
                "--output",
                str(encrypted),
                "--key-file",
                str(key_file),
            ],
        )
        assert result.exit_code == 0
        assert "Stored credential ref 'my-twitter'" in result.output

    def test_credentials_list_outputs_refs(self, runner, tmp_path):
        from soren.services.credentials_service import CredentialStore

        encrypted = tmp_path / "credentials.enc"
        key_file = tmp_path / "credentials.key"
        store = CredentialStore(encrypted, key_path=key_file)
        store.add(
            ref="sproutful",
            service_type="survey_platform",
            username="user@example.com",
            password="secret",
        )

        result = runner.invoke(
            cli,
            [
                "credentials",
                "list",
                "--input",
                str(encrypted),
                "--key-file",
                str(key_file),
            ],
        )
        assert result.exit_code == 0
        assert "sproutful" in result.output


class TestPlaybooksCommands:
    def test_playbooks_list_empty(self, runner):
        """playbooks list returns 0 and message when no playbooks exist."""
        result = runner.invoke(cli, ["playbooks", "list"])
        assert result.exit_code == 0
        assert "No playbooks found" in result.output

    def test_playbooks_show_missing_exits_with_error(self, runner):
        """playbooks show exits 1 when playbook does not exist."""
        result = runner.invoke(cli, ["playbooks", "show", "nonexistent-service"])
        assert result.exit_code == 1
        assert "No playbook found" in result.output or "nonexistent" in result.output.lower()

    def test_playbooks_show_displays_content(self, runner, tmp_path):
        """playbooks show displays content when playbook exists."""
        playbooks_dir = tmp_path / "playbooks"
        playbooks_dir.mkdir()
        (playbooks_dir / "twitter.md").write_text("# Twitter Playbook\n\n## Login\nUse OAuth.")
        config_path = tmp_path / "settings.yaml"
        config_path.write_text(f"playbooks:\n  base_dir: {playbooks_dir}\n")
        result = runner.invoke(cli, ["playbooks", "show", "twitter", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "Twitter Playbook" in result.output or "OAuth" in result.output


class TestPersonasCommands:
    def test_personas_validate(self, runner):
        result = runner.invoke(cli, ["personas", "validate"])
        assert result.exit_code == 0
        assert "Persona Validation" in result.output

    def test_personas_list(self, runner):
        result = runner.invoke(cli, ["personas", "list"])
        assert result.exit_code == 0
        assert "Configured Personas" in result.output


class TestHistoryCommands:
    def test_history_show_reads_report_files(self, runner, tmp_path):
        report_dir = tmp_path / "reports"
        report_dir.mkdir(parents=True)
        (report_dir / "cycle-1.json").write_text(
            '{"cycle_id":"cycle-1","cycle_type":"execute","started_at":"2026-03-01T00:00:00Z",'
            '"total_duration_seconds":4.2,"task_executed_id":"abc123","task_result":{"success":true}}'
        )
        result = runner.invoke(cli, ["history", "show", "--report-dir", str(report_dir)])
        assert result.exit_code == 0
        assert "Recent Cycles" in result.output
        assert "cycle-1" in result.output

    def test_history_show_limit_option(self, runner):
        result = runner.invoke(cli, ["history", "show", "--limit", "25", "--report-dir", "data/reports"])
        assert result.exit_code == 0


class TestUatCommands:
    def test_uat_checklist_renders_smoke_steps(self, runner):
        result = runner.invoke(cli, ["uat", "checklist"])
        assert result.exit_code == 0
        assert "UAT Smoke Checklist" in result.output
        assert "soren cycle run" in result.output
        assert "soren history show --limit 5" in result.output
