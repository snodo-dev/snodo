"""Journey 3: 2+N init + producer task execution.

FILE: tests/e2e/test_2plus_n_journey.py (Task 7.13)
"""

import pytest


@pytest.mark.e2e
def test_2plus_n_init_and_run(snodo_cli):
    r1 = snodo_cli(["init", "--template", "2+n"])
    assert r1.returncode == 0

    # 2+n has strict constraints (files_in_scope, tests_exist).
    # Run with a task that should produce in-scope artifacts.
    r2 = snodo_cli(["run", "implement a user registration endpoint", "--mock"])
    # Warn stubs under unanimous → ESCALATE → exit 1
    assert r2.returncode == 1


@pytest.mark.e2e
def test_2plus_n_protocol_structure(snodo_cli):
    snodo_cli(["init", "--template", "2+n"])

    # Verify protocol file contains expected content
    protocol = snodo_cli.home / ".snodo" / "protocol.yml"
    content = protocol.read_text()
    assert "producer" in content
    assert "reviewer" in content
    assert "2+n" in content


@pytest.mark.e2e
def test_2plus_n_plan_create(snodo_cli):
    snodo_cli(["init", "--template", "2+n"])
    r = snodo_cli(["plan", "create", "build user profile page", "--mock"])
    assert r.returncode == 0
