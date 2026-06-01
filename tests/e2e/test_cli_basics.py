"""Journey 8: Help and version commands.

FILE: tests/e2e/test_cli_basics.py (Task 7.13)
"""

import pytest


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
    stdout = result.stdout
    for subcmd in ("init", "run", "plan", "session", "config", "resolve"):
        assert subcmd in stdout, f"--help missing '{subcmd}'"


@pytest.mark.e2e
def test_run_help(snodo_cli):
    result = snodo_cli(["run", "--help"])
    assert result.returncode == 0
    assert "--protocol" in result.stdout
    assert "--mock" in result.stdout


@pytest.mark.e2e
def test_init_help(snodo_cli):
    result = snodo_cli(["init", "--help"])
    assert result.returncode == 0
    assert "--template" in result.stdout


@pytest.mark.e2e
def test_session_help(snodo_cli):
    result = snodo_cli(["session", "--help"])
    assert result.returncode == 0
    assert "list" in result.stdout


@pytest.mark.e2e
def test_resolve_help(snodo_cli):
    result = snodo_cli(["resolve", "--help"])
    assert result.returncode == 0
    assert "session_id" in result.stdout.lower() or "--decision" in result.stdout
