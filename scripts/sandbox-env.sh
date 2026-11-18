# shellcheck shell=bash
# Soren sandbox environment bootstrap.
#
# Usage (from the project root):
#     source scripts/sandbox-env.sh
#
# Makes every project command (`just all`, `just test`, `uv run soren ...`)
# usable in a Linux sandbox / CI-like shell that lacks the macOS developer
# setup. Safe to source repeatedly (idempotent) and never exits the caller's
# shell on failure -- it prints a diagnostic and returns non-zero instead.
#
# What it does:
#   1. Puts uv's tool bin directory (e.g. ~/.local/bin) on PATH.
#   2. Installs `just` via `uv tool install rust-just` if it is missing.
#   3. Exports UV_PROJECT_ENVIRONMENT=.venv.nosync (matches the justfile, so
#      bare `uv run` and `just` share one environment).
#   4. Runs `uv sync --extra dev` (fast no-op when already in sync).
#   5. Points `.venv` at `.venv.nosync` so pyright (venv = ".venv") resolves it.
#
# Environment knobs:
#   SOREN_SANDBOX_SKIP_SYNC=1   skip step 4 (offline / known-good env)
#
# Sandbox note: the sandbox package index can lag PyPI. If `uv sync` fails
# with a 404 for a locked version, relax the floor in pyproject.toml and run
# `uv lock -P <package>` for just the missing packages (see README/PR notes).

_soren_sandbox_main() {
    local root
    # Resolve project root from this script's location (works from any cwd).
    if [ -n "${BASH_SOURCE[0]:-}" ]; then
        root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    else
        # zsh: %x expands to the file being sourced
        # shellcheck disable=SC2296
        root="$(cd "$(dirname "${(%):-%x}")/.." && pwd)"
    fi

    if [ ! -f "$root/pyproject.toml" ] || [ ! -f "$root/justfile" ]; then
        echo "sandbox-env: could not locate project root (looked in $root)" >&2
        return 1
    fi

    # 1. uv must exist; put its tool bin dir on PATH.
    if ! command -v uv >/dev/null 2>&1; then
        for candidate in "${UV_INSTALL_DIR:-}" "${XDG_BIN_HOME:-}" "$HOME/.local/bin" "$HOME/.cargo/bin"; do
            [ -n "$candidate" ] || continue
            if [ -x "$candidate/uv" ]; then
                export PATH="$candidate:$PATH"
                break
            fi
        done
    fi
    if ! command -v uv >/dev/null 2>&1; then
        echo "sandbox-env: uv not found. Install it: https://docs.astral.sh/uv/" >&2
        return 1
    fi

    local tool_bin
    tool_bin="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")"
    case ":$PATH:" in
        *":$tool_bin:"*) ;;
        *) export PATH="$tool_bin:$PATH" ;;
    esac

    # 2. just
    if ! command -v just >/dev/null 2>&1; then
        echo "sandbox-env: installing just (uv tool install --reinstall rust-just)..."
        local install_log
        if ! install_log="$(uv tool install --reinstall rust-just 2>&1)" || ! command -v just >/dev/null 2>&1; then
            echo "sandbox-env: failed to install just via uv:" >&2
            echo "$install_log" >&2
            return 1
        fi
        hash -r 2>/dev/null || true
    fi

    # 3. Shared project environment (same value the justfile exports).
    export UV_PROJECT_ENVIRONMENT=".venv.nosync"

    # 4. Dependencies
    if [ "${SOREN_SANDBOX_SKIP_SYNC:-0}" != "1" ]; then
        if ! (cd "$root" && uv sync --extra dev --quiet); then
            echo "sandbox-env: 'uv sync --extra dev' failed (see sandbox note in scripts/sandbox-env.sh)" >&2
            return 1
        fi
    fi

    # 5. .venv -> .venv.nosync for pyright / IDEs. Never clobber a real directory.
    if [ -d "$root/.venv" ] && [ ! -L "$root/.venv" ]; then
        echo "sandbox-env: warning: $root/.venv is a real directory; pyright may use it instead of .venv.nosync" >&2
    elif [ -d "$root/.venv.nosync" ]; then
        ln -sfn .venv.nosync "$root/.venv"
    fi

    echo "sandbox-env: ready ($(uv --version), just $(just --version | awk '{print $2}'), $("$root/.venv.nosync/bin/python" --version 2>&1))"
    echo "sandbox-env: try 'just all' or 'just --list'"
    return 0
}

if (return 0 2>/dev/null); then
    _soren_sandbox_main
    _soren_sandbox_rc=$?
    unset -f _soren_sandbox_main
    return $_soren_sandbox_rc
else
    echo "sandbox-env: this script must be sourced: 'source scripts/sandbox-env.sh'" >&2
    exit 1
fi
