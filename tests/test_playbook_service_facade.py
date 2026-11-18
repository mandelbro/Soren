"""PlaybookService facade contract for the deprecated markdown API (delegated to MarkdownPlaybookStore)."""

from __future__ import annotations

import warnings
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.services import playbook_service as playbook_service_module
from soren.services.markdown_playbooks import MarkdownPlaybookStore, PlaybookInfo
from soren.services.playbook_service import PlaybookService


@pytest.fixture
def service(tmp_path: Path) -> PlaybookService:
    settings = MagicMock()
    for attr, sub in [
        ("base_dir", "md"),
        ("site_playbooks_dir", "site"),
        ("host_playbooks_dir", "host"),
        ("base_playbooks_dir", "base"),
    ]:
        setattr(settings.playbooks, attr, str(tmp_path / sub))
    return PlaybookService(settings, MagicMock(), playbooks_dir=tmp_path / "md")


@pytest.mark.parametrize(
    ("method", "args", "message"),
    [
        ("load", ("x",), "load() is deprecated"),
        ("get_relevant_sections", ("## Login\nhi", "survey"), "get_relevant_sections() is deprecated"),
    ],
)
def test_deprecated_methods_warn_at_the_caller(
    service: PlaybookService, method: str, args: tuple, message: str
) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        getattr(service, method)(*args)
    [warning] = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert message in str(warning.message)
    assert warning.filename == __file__  # stacklevel points at the caller, not the facade


def test_markdown_store_does_not_warn_itself(tmp_path: Path) -> None:
    store = MarkdownPlaybookStore(MagicMock(), MagicMock(), tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert store.load("x") is None


async def test_write_back_and_list_forward(service: PlaybookService) -> None:
    sentinel = object()
    with patch.object(service._markdown, "write_back", AsyncMock(return_value=sentinel)) as write_back:
        assert await service.write_back("svc", MagicMock(), [{"a": 1}], dry_run=True) is sentinel
    write_back.assert_awaited_once()
    assert write_back.await_args.kwargs == {"dry_run": True}
    with patch.object(service._markdown, "list_playbooks", return_value=[sentinel]):
        assert service.list_playbooks() == [sentinel]


def test_legacy_names_still_importable_from_playbook_service() -> None:
    assert playbook_service_module.PlaybookInfo is PlaybookInfo
    for name in ("PlaybookUpdate", "TASK_SECTION_MAP", "SOREN_MARKER", "SECTION_HEADER"):
        assert hasattr(playbook_service_module, name)
