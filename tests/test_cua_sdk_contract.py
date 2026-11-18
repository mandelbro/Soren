"""Contract tests against the real CUA SDK.

Most unit tests replace ``cua_agent`` / ``computer`` with lightweight fakes
(the ``fake_cua_sdk`` fixture) because importing the real SDK costs several
seconds. This module is the one place that imports the real SDK, and checks
that the constructor keywords Soren passes still exist, so SDK upgrades that
rename or drop them fail here instead of at runtime in a VM.
"""

from __future__ import annotations

import inspect

import pytest

# Keyword arguments Soren passes (task_executor, task_discovery, training_session, vm_manager).
COMPUTER_AGENT_KWARGS = {"model", "tools", "instructions", "screenshot_delay", "only_n_most_recent_images"}
COMPUTER_KWARGS = {"os_type", "provider_type", "name", "display", "memory"}


def _missing_named_params(callable_obj, names: set[str]) -> set[str]:
    """Names not declared as explicit parameters.

    Checked even when the signature has ``**kwargs``: ComputerAgent does, so a
    renamed parameter would otherwise be silently swallowed and ignored.
    """
    return names - set(inspect.signature(callable_obj).parameters)


@pytest.mark.parametrize(
    ("module", "attr", "kwargs"),
    [("cua_agent", "ComputerAgent", COMPUTER_AGENT_KWARGS), ("computer", "Computer", COMPUTER_KWARGS)],
)
def test_sdk_constructors_accept_soren_kwargs(module: str, attr: str, kwargs: set[str]) -> None:
    cls = getattr(__import__(module), attr)
    missing = _missing_named_params(cls.__init__, kwargs)
    assert not missing, f"{module}.{attr} no longer declares: {sorted(missing)}"


def test_computer_agent_run_is_async_generator() -> None:
    from cua_agent import ComputerAgent

    assert inspect.isasyncgenfunction(ComputerAgent.run)
