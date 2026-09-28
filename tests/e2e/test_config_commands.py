"""Journey 5: Config get/set commands.

FILE: tests/e2e/test_config_commands.py (Task 7.13)
"""

import pytest


@pytest.mark.e2e
def test_config_set_and_get_engine_value(snodo_cli):
    r1 = snodo_cli(["config", "set", "engine.token_ttl_seconds", "1200"])
    assert r1.returncode == 0

    r2 = snodo_cli(["config", "get", "engine.token_ttl_seconds"])
    assert r2.returncode == 0
    assert "1200" in r2.stdout


@pytest.mark.e2e
def test_config_set_out_of_range_rejected(snodo_cli):
    r = snodo_cli(["config", "set", "engine.token_ttl_seconds", "99999"])
    assert r.returncode != 0
    err = r.stderr.lower()
    assert "error" in err or "range" in err or "between" in err


@pytest.mark.e2e
def test_config_set_max_subtask_depth(snodo_cli):
    r = snodo_cli(["config", "set", "engine.max_subtask_depth", "5"])
    assert r.returncode == 0

    r2 = snodo_cli(["config", "get", "engine.max_subtask_depth"])
    assert r2.returncode == 0
    assert "5" in r2.stdout


@pytest.mark.e2e
def test_config_show(snodo_cli):
    r = snodo_cli(["config", "show"])
    assert r.returncode == 0
    # Should show model and/or engine config
    assert "model" in r.stdout.lower() or "api" in r.stdout.lower()


@pytest.mark.e2e
def test_notify_test_alias_prints_deprecation_notice(snodo_cli):
    r = snodo_cli(["notify", "test"])
    assert r.returncode != 0
    assert "deprecated" in r.stderr
    assert "snodo config --notify-test" in r.stderr
    assert "No notification targets configured." in r.stdout


@pytest.mark.e2e
def test_config_notify_test_with_no_targets(snodo_cli):
    r = snodo_cli(["config", "--notify-test"])
    assert r.returncode != 0
    assert "No notification targets configured." in r.stdout
