"""Regression coverage for the suite's environment-preparation tripwire."""

from pathlib import Path

import pytest

from snodo.infrastructure import environment


def test_prepare_environment_refuses_checkout_without_spawning(tmp_path):
    repo_root = Path(__file__).resolve().parent.parent
    with pytest.raises(pytest.fail.Exception, match="test_prepare_environment_refuses_checkout_without_spawning"):
        environment.prepare_environment(repo_root)


def test_prepare_environment_allows_external_target_with_stub_runner(tmp_path):
    target = tmp_path / "project"
    target.mkdir()
    (target / "pyproject.toml").write_text("[project]\nname = 'fixture'\nversion = '0.1.0'\n")
    calls = []

    result = environment.prepare_environment(
        target,
        run_command_fn=lambda command, cwd: (calls.append((command, cwd)) or (0, "ok")),
    )

    assert result.status == "executed"
    assert calls == [("uv sync", str(target))]
