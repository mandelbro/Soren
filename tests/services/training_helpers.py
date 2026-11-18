"""Shared helpers for TrainingSession tests (mock callbacks/VM/LLM, fake CUA agent runs)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from soren.services.training_session import TrainingCallbacks

# Patch targets: apply_all and ComputerAgent are imported lazily inside the step runner.


PATCH_APPLY_ALL = "soren.patches.apply_all"


PATCH_CUA_AGENT = "cua_agent.ComputerAgent"


PATCH_VERIFIER = "soren.services.training_session.TrainingSession._get_verifier"


def mock_verification_result(passed: bool = True, confidence: float = 0.9, reasoning: str | None = None):
    """Create a mock VerificationResult."""
    vr = MagicMock()
    vr.passed = passed
    vr.confidence = confidence
    vr.tier_used = 2
    vr.reasoning = reasoning
    vr.duration_ms = 10.0
    return vr


def mock_verifier(results: list | None = None):
    """Create a mock ActionVerifier that returns results in sequence.

    After the explicit results are consumed, returns a default passing result
    to avoid StopAsyncIteration on unexpected extra calls.
    """
    verifier = AsyncMock()
    if results is None:
        results = [mock_verification_result(passed=True)]
    result_iter = iter(results)
    default = mock_verification_result(passed=True)

    async def _verify_side_effect(*args, **kwargs):
        return next(result_iter, default)

    verifier.verify = AsyncMock(side_effect=_verify_side_effect)
    return verifier


def mock_callbacks(**overrides: AsyncMock) -> TrainingCallbacks:
    """Create a mock callbacks object implementing the protocol."""
    cb = MagicMock(spec=TrainingCallbacks)
    cb.show_screenshot = overrides.get("show_screenshot", AsyncMock())
    cb.get_instruction = overrides.get("get_instruction", AsyncMock(return_value="/done"))
    cb.confirm_step = overrides.get("confirm_step", AsyncMock(return_value="y"))
    cb.get_expected_after = overrides.get("get_expected_after", AsyncMock(return_value="Screen looks correct"))
    cb.show_message = overrides.get("show_message", AsyncMock())
    cb.show_error = overrides.get("show_error", AsyncMock())
    # Autonomous verify+retry callbacks
    cb.get_instruction_with_expected = overrides.get(
        "get_instruction_with_expected",
        AsyncMock(return_value=("/done", "")),
    )
    cb.show_verification_result = overrides.get("show_verification_result", AsyncMock())
    cb.handle_stuck = overrides.get("handle_stuck", AsyncMock(return_value="skip"))
    return cb


def mock_vm() -> AsyncMock:
    vm = AsyncMock()
    vm.ensure_ready = AsyncMock()
    computer = AsyncMock()
    computer.interface.screenshot = AsyncMock(return_value=b"fake-screenshot")
    vm.connect_computer = AsyncMock(return_value=computer)
    vm.prepare_session_environment = AsyncMock()
    return vm


def mock_llm() -> AsyncMock:
    llm = AsyncMock()
    llm.generate = AsyncMock(return_value="A screen with a form")
    return llm


def mock_playbook_service() -> AsyncMock:
    ps = AsyncMock()
    ps.load_site = MagicMock(return_value=None)
    ps.save_flow = MagicMock()
    return ps


def make_cua_plan() -> dict:
    """Build the first SDK yield: VLM planned action (computer_call).

    Mirrors the canonical CUA SDK shape: ``action`` is a sibling of ``type`` at
    the top level of the item (see ``agent/responses.py::make_click_item``).
    """
    return {
        "output": [
            {
                "type": "computer_call",
                "action": {"type": "click", "x": 100, "y": 200, "button": "left"},
            }
        ]
    }


def make_cua_result(screenshot_b64: str = "ZmFrZQ==") -> dict:
    """Build the second SDK yield: action result with screenshot (computer_call_output)."""
    return {
        "output": [
            {
                "type": "computer_call_output",
                "output": {"image_url": f"data:image/png;base64,{screenshot_b64}"},
            }
        ]
    }


def make_cua_completion() -> dict:
    """Build the final SDK yield: assistant completion message."""
    return {
        "output": [
            {
                "role": "assistant",
                "content": "Action completed successfully.",
            }
        ]
    }


async def fake_agent_run(messages: list) -> list[dict]:  # noqa: ARG001
    """Async generator mimicking actual CUA SDK: plan -> execute (screenshot) -> assistant done."""
    yield make_cua_plan()
    yield make_cua_result()
    yield make_cua_completion()


def patched_cua():
    """Return a mock ComputerAgent whose run() accurately mimics the SDK two-phase generator."""
    mock_agent_inst = MagicMock()
    mock_agent_inst.run = MagicMock(side_effect=lambda m: fake_agent_run(m))
    return mock_agent_inst
