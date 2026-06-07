"""Tests for the snodo-prompt lightweight shell-prompt command.

FILE: tests/cli/test_prompt.py

Includes both behavioral tests (correct output for various states)
and an import-graph boundary guard that fails if the prompt module
ever imports compiler, engine, cli.commands, coders, or langchain.
"""

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_PROMPT_FILE = _PROJECT_ROOT / "snodo" / "prompt_cmd.py"

# Top-level packages whose presence in prompt_cmd imports would break
# the lightweight boundary (transitive imports are NOT followed — this
# is a direct-import check only, matching the signing-boundary pattern).
_FORBIDDEN_TOPS = {
    "snodo.compiler",
    "snodo.engine",
    "snodo.coders",
    "snodo.cli.commands",
    "langchain",
    "langchain_core",
    "langchain_community",
    "pydantic",
}


# ------------------------------------------------------------------#
# Import-graph boundary guard (AST — same pattern as test_signing_keys)
# ------------------------------------------------------------------#

def _collect_imports(file_path: Path) -> set:
    """AST-parse *file_path* and return the set of top-level module
    names directly imported (first segment of dotted imports)."""
    try:
        tree = ast.parse(file_path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return set()

    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # Walk all submodule prefixes (e.g. snodo.a.b → snodo, snodo.a, snodo.a.b)
                parts = alias.name.split(".")
                for i in range(1, len(parts) + 1):
                    names.add(".".join(parts[:i]))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                parts = node.module.split(".")
                for i in range(1, len(parts) + 1):
                    names.add(".".join(parts[:i]))
    return names


def _find_forbidden_imports(file_path: Path) -> list:
    """Return [(name,)], the forbidden top-level imports found."""
    imports = _collect_imports(file_path)
    violations = []
    for forbidden in sorted(_FORBIDDEN_TOPS):
        if forbidden in imports:
            violations.append(forbidden)
    return violations


def test_prompt_module_imports_stay_lightweight():
    """snodo/prompt_cmd.py imports ONLY paths + state — no compiler/engine/etc."""
    violations = _find_forbidden_imports(_PROMPT_FILE)
    assert violations == [], (
        "PROMPT MODULE IMPORTS HEAVY PACKAGES:\n"
        + "\n".join(f"  imports {v}" for v in violations)
        + "\n\nThis breaks the lightweight prompt boundary. "
        "The prompt command imports must stay under ~50ms. "
        "Only snodo.infrastructure.paths + snodo.infrastructure.state are allowed."
    )


# ------------------------------------------------------------------#
# Behavioral tests
# ------------------------------------------------------------------#

class TestPromptOutsideProject:
    """Outside a snodo project — silent, exit 0."""

    def test_no_output_outside_project(self, tmp_path):
        result = subprocess.run(
            [sys.executable, "-c", "from snodo.prompt_cmd import main; main()"],
            capture_output=True, text=True, cwd=str(tmp_path),
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert result.stderr.strip() == ""


class TestPromptInsideProject:
    """Inside a project — prints mode (+ session)."""

    def test_mode_only_no_active_session(self, tmp_path):
        _init_project(tmp_path, current_mode="reviewer", active_session={})
        result = _run_prompt(tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == "reviewer"

    def test_mode_with_active_session(self, tmp_path):
        _init_project(
            tmp_path,
            current_mode="producer",
            active_session={"producer": "sess_20260101_prod_a1b2c3"},
        )
        result = _run_prompt(tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == "producer:a1b2c3"

    def test_mode_with_active_session_short_fallback(self, tmp_path):
        """Session id shorter than 6 chars — use full id."""
        _init_project(
            tmp_path,
            current_mode="planner",
            active_session={"planner": "s_x"},
        )
        result = _run_prompt(tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == "planner:s_x"

    def test_different_mode_has_session_but_not_current(self, tmp_path):
        """Active session is for 'reviewer', current_mode is 'producer' — no session shown."""
        _init_project(
            tmp_path,
            current_mode="producer",
            active_session={"reviewer": "sess_some_reviewer_session"},
        )
        result = _run_prompt(tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == "producer"


class TestPromptCorruptState:
    """Corrupt or missing state.json — silent, exit 0."""

    def test_no_state_file(self, tmp_path):
        (tmp_path / ".snodo").mkdir()
        result = _run_prompt(tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_corrupt_state_json(self, tmp_path):
        (tmp_path / ".snodo").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".snodo" / "state.json").write_text("{{{ not valid json")
        result = _run_prompt(tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""


# ------------------------------------------------------------------#
# Helpers
# ------------------------------------------------------------------#

def _init_project(project_dir: Path, current_mode: str,
                  active_session: dict) -> None:
    """Create a minimal .snodo/state.json for testing."""
    snodo_dir = project_dir / ".snodo"
    snodo_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "current_mode": current_mode,
        "active_session": active_session,
        "metadata": {},
    }
    (snodo_dir / "state.json").write_text(json.dumps(state))


def _run_prompt(cwd: Path) -> subprocess.CompletedProcess:
    """Run the prompt command in *cwd* via a subprocess."""
    return subprocess.run(
        [sys.executable, "-c", "from snodo.prompt_cmd import main; main()"],
        capture_output=True, text=True, cwd=str(cwd),
    )
