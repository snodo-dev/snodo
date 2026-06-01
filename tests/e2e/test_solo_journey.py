"""Journey 1: Solo init + simple task.

FILE: tests/e2e/test_solo_journey.py (Task 7.13)
"""


import pytest


@pytest.mark.e2e
def test_solo_init_and_simple_task(snodo_cli, audit_log_entries):
    # Init solo
    r1 = snodo_cli(["init", "--template", "solo"])
    assert r1.returncode == 0
    assert (snodo_cli.home / ".snodo" / "protocol.yml").exists()

    # Run — warn stubs under unanimous → ESCALATE → exit 1
    r2 = snodo_cli(["run", "implement a hello world function", "--mock"])
    assert r2.returncode == 1, f"expected ESCALATE, got: {r2.stderr}"
    assert "STRUCTURED HALT PAYLOAD" in r2.stdout

    # Verify audit log was populated with expected escalation events
    entries = audit_log_entries()
    assert len(entries) > 0, "audit log should not be empty"
    event_types = {e["event_type"] for e in entries}
    assert "disagreement_escalated" in event_types or "halt" in event_types


@pytest.mark.e2e
def test_solo_init_and_task_with_special_chars(snodo_cli):
    r1 = snodo_cli(["init", "--template", "solo"])
    assert r1.returncode == 0

    r2 = snodo_cli(["run", "Implement user login with OAuth2 & JWT", "--mock"])
    assert r2.returncode == 1


@pytest.mark.e2e
def test_solo_init_fails_if_already_exists(snodo_cli):
    r1 = snodo_cli(["init", "--template", "solo"])
    assert r1.returncode == 0

    # Second init without --force should fail
    r2 = snodo_cli(["init", "--template", "solo"])
    assert r2.returncode != 0
    assert "already exists" in r2.stderr.lower() or ".snodo/" in r2.stderr.lower()


@pytest.mark.e2e
def test_solo_init_force_overwrites(snodo_cli):
    r1 = snodo_cli(["init", "--template", "solo"])
    assert r1.returncode == 0
    before = (snodo_cli.home / ".snodo" / "protocol.yml").read_text()

    (snodo_cli.home / ".snodo" / "protocol.yml").write_text("modified")
    r2 = snodo_cli(["init", "--template", "solo", "--force"])
    assert r2.returncode == 0
    after = (snodo_cli.home / ".snodo" / "protocol.yml").read_text()
    assert before == after
