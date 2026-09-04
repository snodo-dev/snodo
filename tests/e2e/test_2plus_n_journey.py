"""Journey 3: 2+N init + producer task execution.

FILE: tests/e2e/test_2plus_n_journey.py (Task 7.13)
"""

import pytest


@pytest.mark.e2e
def test_2plus_n_init_and_run(snodo_cli):
    r1 = snodo_cli(["init", "--template", "2+n", "--yes"])
    assert r1.returncode == 0

    # 2+n has strict global constraints (files_in_scope, tests_exist). The mock
    # coder's fixture files fall outside the declared scope, so the run halts on
    # the constraint blocker (quality's default test command resolves fine now).
    r2 = snodo_cli(["run", "implement a user registration endpoint", "--mock"])
    assert r2.returncode == 1


@pytest.mark.e2e
def test_2plus_n_protocol_structure(snodo_cli):
    snodo_cli(["init", "--template", "2+n", "--yes"])

    # Verify protocol file contains expected content
    protocol = snodo_cli.home / ".snodo" / "protocol.yml"
    content = protocol.read_text()
    assert "producer" in content
    assert "reviewer" in content
    assert "2+n" in content


@pytest.mark.e2e
def test_2plus_n_plan_create(snodo_cli):
    snodo_cli(["init", "--template", "2+n", "--yes"])
    r = snodo_cli(["plan", "create", "build user profile page", "--mock"])
    assert r.returncode == 0
