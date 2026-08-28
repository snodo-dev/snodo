"""Behavioral tests for snodo status CLI command (snodo/cli/commands/status_cmd.py).

FILE: tests/cli/test_status_cmd.py
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
import typer
import yaml

from snodo.cli.commands.status_cmd import (
    _read_protocol,
    _session_outcome,
    register,
    status_command,
)
from snodo.infrastructure.session import SessionManager
from snodo.infrastructure.state import ProjectState, write_state


# ============================================================================
# 1. Registration & Registration Entrypoint Tests
# ============================================================================

def test_status_register():
    """register() registers status command on typer App."""
    app = typer.Typer()
    register(app)
    command_names = [cmd.name or cmd.callback.__name__ for cmd in app.registered_commands]
    assert "status" in command_names


def test_status_command_not_in_project(monkeypatch):
    """status_command raises SystemExit when project root cannot be resolved."""
    def _fail_root():
        raise SystemExit(1)

    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", _fail_root)
    args = SimpleNamespace(json=False)
    with pytest.raises(SystemExit):
        status_command(args)


# ============================================================================
# 2. Text Output Tests (Happy Path & Empty Path)
# ============================================================================

def test_status_command_happy_path(tmp_path, capsys, monkeypatch):
    """status_command prints protocol, mode, session, last run, and inspect options."""
    project_root = str(tmp_path)
    home_dir = tmp_path / "home"
    monkeypatch.setenv("SNODO_HOME", str(home_dir))
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: project_root)

    # 1. Write .snodo/protocol.yml
    snodo_dir = tmp_path / ".snodo"
    snodo_dir.mkdir()
    protocol_data = {"protocol_id": "p_team_v1", "name": "Team Protocol"}
    (snodo_dir / "protocol.yml").write_text(yaml.dump(protocol_data))

    # 2. Create session & write .snodo/state.json
    mgr = SessionManager()
    session = mgr.create_session("producer", project_root)

    state = ProjectState(
        current_mode="producer",
        active_session={"producer": session.session_id},
    )
    write_state(project_root, state)

    args = SimpleNamespace(json=False)
    res = status_command(args)
    assert res == 0

    out = capsys.readouterr().out
    assert "Protocol: Team Protocol (p_team_v1)" in out
    assert "Mode:     producer" in out
    assert f"Session:  {session.session_id}" in out
    assert f"Last run: {session.session_id}" in out
    assert "Inspect:" in out
    assert f"snodo session show {session.session_id}" in out


def test_status_command_empty_no_session(tmp_path, capsys, monkeypatch):
    """status_command formats empty/none outputs when protocol and session are absent."""
    project_root = str(tmp_path)
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: project_root)

    # State with no mode and no active session
    write_state(project_root, ProjectState())

    args = SimpleNamespace(json=False)
    res = status_command(args)
    assert res == 0

    out = capsys.readouterr().out
    assert "Protocol: (no protocol.yml)" in out
    assert "Mode:     (none)" in out
    assert "Session:  (none)" in out
    assert "Last run: (none)" in out


# ============================================================================
# 3. --json Machine-Readable Output Tests
# ============================================================================

def test_status_command_json_output(tmp_path, capsys, monkeypatch):
    """status_command with json=True emits valid status JSON structure."""
    project_root = str(tmp_path)
    home_dir = tmp_path / "home"
    monkeypatch.setenv("SNODO_HOME", str(home_dir))
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: project_root)

    snodo_dir = tmp_path / ".snodo"
    snodo_dir.mkdir()
    protocol_data = {"protocol_id": "p_solo", "name": "Solo Protocol"}
    (snodo_dir / "protocol.yml").write_text(yaml.dump(protocol_data))

    mgr = SessionManager()
    session = mgr.create_session("dev", project_root)

    state = ProjectState(
        current_mode="dev",
        active_session={"dev": session.session_id},
    )
    write_state(project_root, state)

    args = SimpleNamespace(json=True)
    res = status_command(args)
    assert res == 0

    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["ok"] is True
    assert data["project_root"] == project_root
    assert data["protocol"]["id"] == "p_solo"
    assert data["protocol"]["name"] == "Solo Protocol"
    assert data["mode"] == "dev"
    assert data["active_session"] == session.session_id
    assert data["last_run"]["session_id"] == session.session_id


# ============================================================================
# 4. Helper Function Unit Tests
# ============================================================================

def test_read_protocol_edge_cases(tmp_path):
    """_read_protocol handles missing, corrupt, or non-dict protocol.yml."""
    # Missing protocol.yml
    assert _read_protocol(str(tmp_path)) == ("(no protocol.yml)", "")

    snodo_dir = tmp_path / ".snodo"
    snodo_dir.mkdir()
    proto_file = snodo_dir / "protocol.yml"

    # Corrupt YAML
    proto_file.write_text("invalid: [yaml: :")
    assert _read_protocol(str(tmp_path)) == ("(unreadable)", "")

    # Non-dict YAML
    proto_file.write_text("- item1\n- item2")
    assert _read_protocol(str(tmp_path)) == ("(unreadable)", "")


def test_session_outcome_derivation():
    """_session_outcome derives outcome strings from session checkpoint decisions."""
    # 1. Halt decision priority
    session_halt = MagicMock()
    session_halt.checkpoint.decisions = {
        "halt": {
            "task_1": {"final_decision": "blocker"},
            "task_2": {"final_decision": "escalate"},
        }
    }
    assert _session_outcome(session_halt) == "escalate"

    # 2. Task failure priority
    session_failure = MagicMock()
    session_failure.checkpoint.decisions = {
        "task_failure": {"task_1": {"attempt": 1}}
    }
    assert _session_outcome(session_failure) == "failed"

    # 3. Current task in progress
    session_prog = MagicMock()
    session_prog.checkpoint.decisions = {}
    session_prog.checkpoint.current_task = "task_in_progress"
    assert _session_outcome(session_prog) == "in progress"

    # 4. No tasks
    session_empty = MagicMock()
    session_empty.checkpoint.decisions = {}
    session_empty.checkpoint.current_task = None
    assert _session_outcome(session_empty) == "no tasks"
