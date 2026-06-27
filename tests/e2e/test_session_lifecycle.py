"""Journey 4: Session lifecycle.

FILE: tests/e2e/test_session_lifecycle.py (Task 7.13)
"""

import pytest


@pytest.mark.e2e
def test_session_auto_create_and_list(snodo_cli):
    # Init and run, session auto-created
    snodo_cli(["init", "--template", "solo"])
    snodo_cli(["run", "task A", "--mock"])

    result = snodo_cli(["session", "list"])
    assert result.returncode == 0
    # After 7.19: no status field — just verify a session appears
    assert "Sessions:" in result.stdout
    assert any(line.strip().startswith("sess_") for line in result.stdout.splitlines())


@pytest.mark.e2e
def test_session_delete(snodo_cli):
    snodo_cli(["init", "--template", "solo"])
    snodo_cli(["run", "task A", "--mock"])

    # Find session ID from session list output
    r = snodo_cli(["session", "list"])
    lines = r.stdout.strip().splitlines()
    # Session ID format: sess_YYYYMMDD_prod_XXXXXX
    session_id = None
    for line in lines:
        if line.strip().startswith("sess_"):
            session_id = line.strip().split()[0]
            break
    assert session_id is not None, f"No session ID found in: {r.stdout}"

    # Delete the session (7.19: "close" renamed to "delete")
    r2 = snodo_cli(["session", "delete", session_id])
    assert r2.returncode == 0

    # Verify the session no longer appears in the list
    r3 = snodo_cli(["session", "list"])
    assert r3.returncode == 0
    lines_after = [ln for ln in r3.stdout.strip().splitlines() if ln.strip().startswith("sess_")]
    assert session_id not in " ".join(lines_after), f"Session {session_id} still present after delete"


@pytest.mark.e2e
def test_session_show(snodo_cli):
    snodo_cli(["init", "--template", "solo"])
    snodo_cli(["run", "task A", "--mock"])

    r = snodo_cli(["session", "list"])
    lines = r.stdout.strip().splitlines()
    session_id = None
    for line in lines:
        if line.strip().startswith("sess_"):
            session_id = line.strip().split()[0]
            break
    assert session_id is not None

    r2 = snodo_cli(["session", "show", session_id])
    assert r2.returncode == 0
    assert session_id in r2.stdout


@pytest.mark.e2e
def test_session_prune(snodo_cli):
    snodo_cli(["init", "--template", "solo"])
    snodo_cli(["run", "task A", "--mock"])

    r = snodo_cli(["session", "prune"])
    # Prune should succeed (may or may not remove sessions depending on age)
    assert r.returncode == 0
