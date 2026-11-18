"""End to end: a password the agent types never reaches a write-back prompt.

The agent (local UI-TARS) receives the decrypted login and types it. The step
log the executor records, and both prompts built from it for the reasoning
model (playbook write-back, auto-playbook), must contain only placeholders.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soren.services import credential_redaction
from soren.services.auto_playbook import auto_generate_site_playbook
from soren.services.config_loader import SorenSettings
from soren.services.markdown_playbooks import MarkdownPlaybookStore
from soren.services.service_registry import ServiceRegistry
from soren.services.task_executor import TaskExecutor
from tests.task_executor_helpers import make_persona_with_source, make_task

pytestmark = pytest.mark.usefixtures("fake_cua_sdk")

USERNAME = "analyst@example.com"
PASSWORD = "hunter2-Secret!"


async def _login_run(*_args, **_kwargs):
    """The agent types the username and password, explaining itself, then finishes."""
    yield {
        "output": [
            {
                "type": "computer_call",
                "action": {"type": "type", "text": USERNAME},
                "summary": f"Typing the username {USERNAME}",
            }
        ]
    }
    yield {
        "output": [
            {
                "type": "computer_call",
                "action": {"type": "type", "text": PASSWORD},
                "summary": f"Now the password {PASSWORD}",
            }
        ]
    }
    yield {"output": [{"role": "assistant", "content": f"Task complete. Logged in with {PASSWORD}."}]}


async def _execute_login(tmp_path: Path):
    store = MagicMock()
    store.get.return_value = MagicMock(username=USERNAME, password=PASSWORD)
    executor = TaskExecutor(
        SorenSettings(),
        MagicMock(),
        MagicMock(),
        screenshot_dir=tmp_path / "shots",
        credential_store=store,
        service_registry=ServiceRegistry(tmp_path / "services"),
    )
    with patch("cua_agent.ComputerAgent") as agent_cls:
        agent_cls.return_value.run = _login_run
        result = await executor.execute(make_task(), make_persona_with_source("acmepanel"), MagicMock())
    return result, agent_cls


async def test_agent_still_receives_the_credentials(tmp_path: Path) -> None:
    """Redaction is for recorded data only; the local agent must still be able to log in."""
    _, agent_cls = await _execute_login(tmp_path)

    instructions = agent_cls.call_args.kwargs["instructions"]
    assert PASSWORD in instructions
    assert USERNAME in instructions


async def test_step_log_holds_placeholders_not_credentials(tmp_path: Path) -> None:
    result, _ = await _execute_login(tmp_path)

    assert result.success
    assert PASSWORD not in repr(result.step_log)
    assert USERNAME not in repr(result.step_log)
    assert result.step_log[1]["action_data"]["text"] == "{credential:password}"


async def test_playbook_write_back_prompt_has_no_credentials(tmp_path: Path) -> None:
    result, _ = await _execute_login(tmp_path)
    llm = MagicMock()
    llm.generate = AsyncMock(return_value="1. Log in")
    (tmp_path / "playbooks").mkdir()
    store = MarkdownPlaybookStore(SorenSettings(), llm, tmp_path / "playbooks")

    await store.write_back("acmepanel", make_task(), result.step_log)

    prompt = llm.generate.call_args.kwargs["prompt"]
    assert "{credential:password}" in prompt  # the step is still described
    assert PASSWORD not in prompt
    assert USERNAME not in prompt


async def test_auto_playbook_prompt_has_no_credentials(tmp_path: Path) -> None:
    result, _ = await _execute_login(tmp_path)
    llm = MagicMock()
    llm.generate = AsyncMock(return_value="[]")

    await auto_generate_site_playbook(llm, result.step_log, "acmepanel", "https://acmepanel.example", "execution")

    prompt = llm.generate.call_args.kwargs["prompt"]
    assert PASSWORD not in prompt
    assert USERNAME not in prompt


async def test_handed_off_password_is_registered_for_the_cloud_backstop(tmp_path: Path) -> None:
    await _execute_login(tmp_path)

    assert credential_redaction.contains_registered_secret(f"x {PASSWORD} y")
    # A username may reach a cloud model via screenshots/extracted text (RFC); it must not block those calls.
    assert not credential_redaction.contains_registered_secret(f"Welcome back, {USERNAME}")
