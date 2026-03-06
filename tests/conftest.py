"""Shared test fixtures for Soren tests."""

from pathlib import Path

import pytest


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
