"""Tests for the Soren CLI skeleton."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from soren.cli import cli
from soren.models.vm import VMConfig

SUBCOMMAND_GROUPS = ["cycle", "discover", "tasks", "vm", "credentials", "playbooks", "personas", "history"]


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
    def test_cycle_run_stub(self, runner):
        result = runner.invoke(cli, ["cycle", "run"])
        assert result.exit_code == 0
        assert "Not yet implemented" in result.output


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
    def test_credentials_encrypt_stub(self, runner):
        result = runner.invoke(cli, ["credentials", "encrypt"])
        assert result.exit_code == 0
        assert "Not yet implemented" in result.output

    def test_credentials_add_accepts_args(self, runner):
        result = runner.invoke(cli, ["credentials", "add", "my-twitter", "twitter"])
        assert result.exit_code == 0
        assert "Not yet implemented" in result.output

    def test_credentials_list_stub(self, runner):
        result = runner.invoke(cli, ["credentials", "list"])
        assert result.exit_code == 0
        assert "Not yet implemented" in result.output


class TestPlaybooksCommands:
    def test_playbooks_list_stub(self, runner):
        result = runner.invoke(cli, ["playbooks", "list"])
        assert result.exit_code == 0
        assert "Not yet implemented" in result.output

    def test_playbooks_show_accepts_service(self, runner):
        result = runner.invoke(cli, ["playbooks", "show", "twitter"])
        assert result.exit_code == 0
        assert "Not yet implemented" in result.output


class TestPersonasCommands:
    def test_personas_validate_stub(self, runner):
        result = runner.invoke(cli, ["personas", "validate"])
        assert result.exit_code == 0
        assert "Not yet implemented" in result.output

    def test_personas_list_stub(self, runner):
        result = runner.invoke(cli, ["personas", "list"])
        assert result.exit_code == 0
        assert "Not yet implemented" in result.output


class TestHistoryCommands:
    def test_history_show_stub(self, runner):
        result = runner.invoke(cli, ["history", "show"])
        assert result.exit_code == 0
        assert "Not yet implemented" in result.output

    def test_history_show_limit_option(self, runner):
        result = runner.invoke(cli, ["history", "show", "--limit", "25"])
        assert result.exit_code == 0
