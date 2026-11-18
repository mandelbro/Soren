"""Shared test fixtures for Soren tests."""

import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# The CUA SDK sends PostHog telemetry at import time (cua_core.telemetry). Tests must
# not phone home, and the blocking sends add ~14s to the first SDK import. It is
# already off by default in CI; respect an explicit developer setting.
os.environ.setdefault("CUA_TELEMETRY", "off")


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Create an isolated data directory for tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "playbooks").mkdir()
    (data_dir / "reports").mkdir()
    (data_dir / "logs").mkdir()
    return data_dir


@pytest.fixture
def tmp_config_dir(tmp_path: Path) -> Path:
    """Create an isolated config directory for tests."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "personas").mkdir()
    return config_dir


@pytest.fixture(autouse=True)
def services_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate every test from the real config/services/ (ADR-0002).

    Service files there are user-specific and gitignored, so code paths that
    use the default ``ServiceRegistry()`` must not see them in tests. Tests
    that want service files can write into this (initially empty) directory.
    Tests that need the real folder pass an explicit path.
    """
    directory = tmp_path_factory.mktemp("services")
    monkeypatch.setattr("soren.services.service_registry.get_services_dir", lambda: directory)
    return directory


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch):
    """Factory: ``fake_clock(module, ...)`` puts those modules on a virtual clock (see tests/fake_clock.py)."""
    from tests.fake_clock import FakeClock

    clock = FakeClock()

    def install(*modules):
        return clock.install(monkeypatch, *modules)

    return install


@pytest.fixture
def fake_cua_sdk(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    """Replace the CUA SDK with lightweight fakes for unit tests (opt-in).

    Installs fake ``cua_agent`` and ``computer`` modules in ``sys.modules`` and
    makes ``soren.patches.apply_all`` a no-op. Importing the real SDK costs
    ~7s, and ``apply_all`` mutates transformers/mlx globally. Existing
    ``patch("cua_agent.ComputerAgent")`` / ``patch("computer.Computer")`` calls
    patch the fakes transparently. The real SDK is covered by
    ``tests/test_cua_sdk_contract.py`` and ``tests/test_patches.py``.
    """
    import soren.patches

    agent_module = types.ModuleType("cua_agent")
    agent_module.ComputerAgent = MagicMock(name="ComputerAgent")  # type: ignore[attr-defined]
    computer_module = types.ModuleType("computer")
    computer_module.Computer = MagicMock(name="Computer")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cua_agent", agent_module)
    monkeypatch.setitem(sys.modules, "computer", computer_module)
    monkeypatch.setattr(soren.patches, "apply_all", lambda: None)
    return types.SimpleNamespace(cua_agent=agent_module, computer=computer_module)


@pytest.fixture
def vnc_virtual_clock(fake_clock):
    """Run soren.patches.vnc_screenshot's sleeps (cursor warp) on a virtual clock."""
    from soren.patches import vnc_screenshot

    return fake_clock(vnc_screenshot)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the run if a test created ./MagicMock (a mocked path used as a real directory)."""
    stray = Path(session.config.rootpath) / "MagicMock"
    if stray.exists():
        session.config.get_terminal_writer().line(
            f"\nERROR: tests created {stray} (a MagicMock used as a filesystem path); give the test real tmp paths.",
            red=True,
        )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
