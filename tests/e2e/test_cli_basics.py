"""Journey 8: Help and version commands.

FILE: tests/e2e/test_cli_basics.py (Task 7.13)
"""

import re

import pytest


@pytest.mark.e2e
def test_subprocess_ignores_inherited_job_project(snodo_cli, monkeypatch, tmp_path):
    """An e2e child gets no job context and cannot touch the dispatching project."""
    other_project = tmp_path / "dispatching-project"
    (other_project / ".snodo").mkdir(parents=True)
    sentinel = other_project / ".snodo" / "sentinel"
    sentinel.write_text("untouched")
    (snodo_cli.home / ".snodo").mkdir()
    from snodo.paths import JOB_CONTEXT_ENV_VARS

    for key in JOB_CONTEXT_ENV_VARS:
        monkeypatch.setenv(key, str(other_project) if key == "SNODO_PROJECT_ROOT" else "outer-job-value")

    result = snodo_cli(["--version"])
    assert result.returncode == 0, result.stderr
    assert not set(JOB_CONTEXT_ENV_VARS) & snodo_cli.last_env.keys()
    assert sentinel.read_text() == "untouched"
    assert list((other_project / ".snodo").iterdir()) == [sentinel]


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
    # version should be semver-ish; a git checkout adds a local build label
    # such as "+b1990.g18615b3", which is not part of the release number
    ver = version_parts[1].split("+", 1)[0]
    assert ver and all(c.isdigit() or c == "." for c in ver)


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
