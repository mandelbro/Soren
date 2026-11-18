"""Enforce the file-size policy (scripts/check_file_sizes.py): no .py file over 500 lines.

Files already over the limit are allowlisted with a ceiling they may not grow past.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_file_sizes.py"
_spec = importlib.util.spec_from_file_location("check_file_sizes", _SCRIPT)
assert _spec and _spec.loader
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def test_repository_within_file_size_limits() -> None:
    problems = check.violations(check.scan())
    assert not problems, "File-size policy violations:\n" + "\n".join(problems)


class TestViolationRules:
    def test_new_file_over_limit(self) -> None:
        assert check.violations({"src/a.py": 501}, {}) == ["src/a.py: 501 lines exceeds the 500-line limit; split it"]

    def test_at_limit_is_fine(self) -> None:
        assert check.violations({"src/a.py": 500, "src/b.py": 401}, {}) == []

    def test_allowlisted_file_may_not_grow(self) -> None:
        [problem] = check.violations({"src/big.py": 701}, {"src/big.py": 700})
        assert "grew past its allowlisted ceiling of 700" in problem

    def test_allowlisted_file_may_shrink(self) -> None:
        assert check.violations({"src/big.py": 650}, {"src/big.py": 700}) == []

    def test_allowlist_entry_must_be_removed_once_within_limit(self) -> None:
        [problem] = check.violations({"src/big.py": 480}, {"src/big.py": 700})
        assert "remove it from OVERSIZE_ALLOWLIST" in problem

    def test_allowlist_entry_for_missing_file(self) -> None:
        [problem] = check.violations({}, {"src/gone.py": 700})
        assert "no longer exists" in problem

    def test_scan_covers_src_tests_scripts(self) -> None:
        sizes = check.scan()
        assert "src/soren/cli/__init__.py" in sizes
        assert "tests/conftest.py" in sizes
        assert "scripts/check_file_sizes.py" in sizes
        assert not any("__pycache__" in p for p in sizes)
