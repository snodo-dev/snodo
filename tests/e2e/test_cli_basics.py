"""Journey 8: Help and version commands.

FILE: tests/e2e/test_cli_basics.py (Task 7.13)
"""

import json
import os
import re
import subprocess
import sys

import pytest


@pytest.mark.e2e
def test_subprocess_ignores_inherited_job_project(snodo_cli, monkeypatch, tmp_path):
    """An e2e child resolves its fixture project despite a dispatch context."""
    other_project = tmp_path / "dispatching-project"
    (other_project / ".snodo").mkdir(parents=True)
    (snodo_cli.home / ".snodo").mkdir()
    monkeypatch.setenv("SNODO_PROJECT_ROOT", str(other_project))
    monkeypatch.setenv("SNODO_JOB_ID", "j_outer")

    result = snodo_cli(["--version"])
    assert result.returncode == 0, result.stderr

    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    for key in (
        "SNODO_PROJECT_ROOT",
        "SNODO_JOB_ID",
        "SNODO_WORKTREE_PATH",
        "SNODO_PLAN_JOB",
        "SNODO_TASK_PLAN",
        "SNODO_TASK_PLAN_WAVE",
        "SNODO_BENCHMARK",
    ):
        env.pop(key, None)
    resolved = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from snodo.paths import resolve_project_root; "
            "print(json.dumps(resolve_project_root()))",
        ],
        cwd=snodo_cli.home,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(resolved.stdout) == str(snodo_cli.home)


def _strip_ansi(text):
    return re.sub(r'\x1b\[[0-9;]*[mGKH]', '', text)


@pytest.mark.e2e
def test_version(snodo_cli):
    result = snodo_cli(["--version"])
    assert result.returncode == 0
    assert result.stdout.strip().startswith("snodo ")
    version_parts = result.stdout.strip().split()
    assert len(version_parts) == 2
    assert version_parts[0] == "snodo"
    # version should be semver-ish
    ver = version_parts[1]
    assert all(c.isdigit() or c == "." for c in ver)


@pytest.mark.e2e
def test_top_level_help(snodo_cli):
    result = snodo_cli(["--help"])
    assert result.returncode == 0
    stdout = _strip_ansi(result.stdout)
    for subcmd in ("init", "run", "plan", "session", "config"):
        assert subcmd in stdout, f"--help missing '{subcmd}'"


@pytest.mark.e2e
def test_run_help(snodo_cli):
    result = snodo_cli(["run", "--help"])
    assert result.returncode == 0
    clean = _strip_ansi(result.stdout)
    assert "--protocol" in clean
    assert "--mock" in clean


@pytest.mark.e2e
def test_init_help(snodo_cli):
    result = snodo_cli(["init", "--help"])
    assert result.returncode == 0
    clean = _strip_ansi(result.stdout)
    assert "--template" in clean


@pytest.mark.e2e
def test_session_help(snodo_cli):
    result = snodo_cli(["session", "--help"])
    assert result.returncode == 0
    assert "list" in _strip_ansi(result.stdout)
