"""The project_announced event (Fixes #214).

A consumer building dimensions from the event stream needs a parent row for
the project_id stamped on every event. project_announced carries identity,
scope and display name once per session, so a fresh project's first run
announces it and a later run in the same project re-announces it.
"""

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def _project_announced(entries):
    return [
        e for e in entries
        if e.get("event_type") == "project_announced"
    ]


def test_project_announced_once_per_session(snodo_cli):
    """A task run in a fresh project emits project_announced exactly once in
    the session's audit log, carrying the resolved identity and scope."""
    tmp_dir = snodo_cli.home
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:myorg/myrepo.git"],
        cwd=str(tmp_dir),
        check=True,
    )

    r = snodo_cli(["init", "--template", "solo", "--yes"])
    assert r.returncode == 0

    r2 = snodo_cli(["run", "implement a hello world function", "--mock"])
    assert r2.returncode == 0, r2.stderr

    entries = []
    audit_path = Path(tmp_dir) / ".snodo" / "audit.log"
    for line in audit_path.read_text().splitlines():
        if line.strip():
            entries.append(json.loads(line))

    announced = _project_announced(entries)
    assert len(announced) == 1, f"expected exactly one project_announced, got {len(announced)}"
    data = announced[0]["data"]
    assert data["project_id"] == "github.com/myorg/myrepo"
    assert data["scope"] == "remote"
    assert data["display_name"] == "project"
    assert "project_root" not in data
    assert "session_id" not in data


def test_project_announced_again_on_second_run_with_existing_session(snodo_cli):
    """A second run adopting the existing active session re-announces the project (Fixes #219)."""
    tmp_dir = snodo_cli.home
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:myorg/myrepo.git"],
        cwd=str(tmp_dir),
        check=True,
    )

    r = snodo_cli(["init", "--template", "solo", "--yes"])
    assert r.returncode == 0

    # Run 1: creates session, emits project_announced (1st)
    r_run1 = snodo_cli(["run", "implement a hello world function", "--mock"])
    assert r_run1.returncode == 0, r_run1.stderr

    # Run 2: adopts existing active session, emits project_announced (2nd)
    snodo_cli(["run", "implement a second feature", "--mock"])

    entries = []
    audit_path = Path(tmp_dir) / ".snodo" / "audit.log"
    for line in audit_path.read_text().splitlines():
        if line.strip():
            entries.append(json.loads(line))

    announced = _project_announced(entries)
    assert len(announced) == 2, f"expected two project_announced, got {len(announced)}"
    for e in announced:
        assert e["data"]["project_id"] == "github.com/myorg/myrepo"
        assert e["data"]["scope"] == "remote"
        assert e["data"]["display_name"] == "project"


def test_project_announced_on_explicit_session_resume(snodo_cli):
    """An explicit --resume run emits project_announced (Fixes #219)."""
    tmp_dir = snodo_cli.home
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:myorg/myrepo.git"],
        cwd=str(tmp_dir),
        check=True,
    )

    r = snodo_cli(["init", "--template", "solo", "--yes"])
    assert r.returncode == 0

    snodo_cli(["run", "implement a hello world function", "--mock"])

    r_list = snodo_cli(["session", "list"])
    session_id = None
    for line in r_list.stdout.splitlines():
        if line.strip().startswith("sess_"):
            session_id = line.strip().split()[0]
            break
    assert session_id is not None, f"No session ID found in: {r_list.stdout}"

    snodo_cli(["run", "implement feature with resume", "--mock", "--resume", session_id])

    entries = []
    audit_path = Path(tmp_dir) / ".snodo" / "audit.log"
    for line in audit_path.read_text().splitlines():
        if line.strip():
            entries.append(json.loads(line))

    announced = _project_announced(entries)
    assert len(announced) == 2, f"expected two project_announced, got {len(announced)}"
    for e in announced:
        assert e["data"]["project_id"] == "github.com/myorg/myrepo"
        assert e["data"]["scope"] == "remote"

