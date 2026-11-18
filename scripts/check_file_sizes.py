#!/usr/bin/env python3
"""Report and enforce Python file-size limits for src/, tests/, and scripts/.

Policy: plan a split once a file passes WARN_LINES; no file may exceed
MAX_LINES. Files that were already over the limit when this check was added
are listed in OVERSIZE_ALLOWLIST with a ceiling equal to their size at that
time. They may shrink but not grow, and each entry must be removed once its
file is back under the limit (a ratchet: the allowlist only gets shorter).

Usage: ``just file-sizes`` (or ``python scripts/check_file_sizes.py``).
Exit code 1 on any violation. Enforced in the test suite by tests/test_file_sizes.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

WARN_LINES = 400
MAX_LINES = 500
ROOTS = ("src", "tests", "scripts")
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# path (relative to the project root) -> ceiling in lines. Only ever remove entries.
OVERSIZE_ALLOWLIST: dict[str, int] = {}


def count_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def scan(project_root: Path = PROJECT_ROOT) -> dict[str, int]:
    """Line counts for every .py file under ROOTS, keyed by project-relative POSIX path."""
    sizes: dict[str, int] = {}
    for root in ROOTS:
        base = project_root / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            sizes[path.relative_to(project_root).as_posix()] = count_lines(path)
    return sizes


def violations(sizes: dict[str, int], allowlist: dict[str, int] = OVERSIZE_ALLOWLIST) -> list[str]:
    problems: list[str] = []
    for path, lines in sorted(sizes.items()):
        ceiling = allowlist.get(path)
        if ceiling is None and lines > MAX_LINES:
            problems.append(f"{path}: {lines} lines exceeds the {MAX_LINES}-line limit; split it")
        elif ceiling is not None and lines > ceiling:
            problems.append(f"{path}: {lines} lines grew past its allowlisted ceiling of {ceiling}; split it")
        elif ceiling is not None and lines <= MAX_LINES:
            problems.append(f"{path}: now {lines} lines (within the limit); remove it from OVERSIZE_ALLOWLIST")
    for path in sorted(set(allowlist) - set(sizes)):
        problems.append(f"{path}: allowlisted but no longer exists; remove it from OVERSIZE_ALLOWLIST")
    return problems


def main() -> int:
    sizes = scan()
    large = [(lines, path) for path, lines in sizes.items() if lines > WARN_LINES]
    if large:
        print(f"Files over {WARN_LINES} lines (plan a split; limit {MAX_LINES}):")
        for lines, path in sorted(large, reverse=True):
            tag = " [allowlisted]" if path in OVERSIZE_ALLOWLIST else ""
            print(f"  {lines:5}  {path}{tag}")
    else:
        print(f"No files over {WARN_LINES} lines.")
    problems = violations(sizes)
    for problem in problems:
        print(f"ERROR: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
