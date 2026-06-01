"""Journey 2: Team init + plan create + plan list.

FILE: tests/e2e/test_team_journey.py (Task 7.13)
"""

import os

import pytest


@pytest.mark.e2e
def test_team_init_and_plan_create(snodo_cli):
    # Init team template
    r1 = snodo_cli(["init", "--template", "team"])
    assert r1.returncode == 0

    # Plan create (requires .snodo/ directory to exist)
    cwd = snodo_cli.home
    os.chdir(str(cwd))
    r2 = snodo_cli(["plan", "create", "build authentication module", "--mock"])
    assert r2.returncode == 0, f"plan create failed: {r2.stderr}"
    assert "Plan created" in r2.stdout


@pytest.mark.e2e
def test_team_plan_list(snodo_cli):
    snodo_cli(["init", "--template", "team"])
    snodo_cli(["plan", "create", "build feature X", "--mock"])

    r = snodo_cli(["plan", "list"])
    assert r.returncode == 0
    assert "build_feature_x" in r.stdout.lower().replace(" ", "_") or "build" in r.stdout.lower()


@pytest.mark.e2e
def test_team_init_then_run_simple_task(snodo_cli):
    snodo_cli(["init", "--template", "team"])
    r = snodo_cli(["run", "a simple task", "--mock"])
    assert r.returncode == 1
