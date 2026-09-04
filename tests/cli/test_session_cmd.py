"""Tests for session CLI commands (snodo session list/show/delete/prune).

FILE: tests/cli/test_session_cmd.py
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from snodo.infrastructure.session import SessionManager

from snodo.cli.commands.session_cmd import session_command

PROJECT_ROOT = "/Users/test/Dev/myproject"


@pytest.fixture
def sessions_dir(tmp_path):
    d = tmp_path / "sessions"
    d.mkdir()
    return d


@pytest.fixture
def mgr(sessions_dir):
    return SessionManager(sessions_dir=sessions_dir)


# ========== LIST ==========

class TestSessionList:
    def test_list_empty(self, mgr, capsys):
        args = SimpleNamespace(
            session_action="list", mode=None, project=None,
            sessions_dir=mgr.sessions_dir,
        )
        result = session_command(args)
        assert result == 0
        assert "No sessions found" in capsys.readouterr().out

    def test_list_shows_sessions(self, mgr, capsys):
        mgr.create_session("producer", PROJECT_ROOT)
        mgr.create_session("reviewer", PROJECT_ROOT)
        args = SimpleNamespace(
            session_action="list", mode=None, project=None,
            sessions_dir=mgr.sessions_dir,
        )
        result = session_command(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "producer" in out
        assert "reviewer" in out

    def test_list_filter_mode(self, mgr, capsys):
        mgr.create_session("producer", PROJECT_ROOT)
        mgr.create_session("reviewer", PROJECT_ROOT)
        args = SimpleNamespace(
            session_action="list", mode="producer", project=None,
            sessions_dir=mgr.sessions_dir,
        )
        result = session_command(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "producer" in out
        assert "reviewer" not in out


# ========== SHOW ==========

class TestSessionShow:
    def test_show_session(self, mgr, capsys):
        session = mgr.create_session("producer", PROJECT_ROOT)
        args = SimpleNamespace(
            session_action="show", session_id=session.session_id,
            sessions_dir=mgr.sessions_dir,
        )
        result = session_command(args)
        assert result == 0
        out = capsys.readouterr().out
        assert session.session_id in out
        assert "producer" in out

    def test_show_nonexistent(self, mgr, capsys):
        args = SimpleNamespace(
            session_action="show", session_id="nonexistent",
            sessions_dir=mgr.sessions_dir,
        )
        result = session_command(args)
        assert result == 1
        assert "not found" in capsys.readouterr().err

    def test_show_audited_but_missing(self, mgr, tmp_path, capsys):
        """A session id the audit log cites but this store lacks is surfaced as
        a cross-home divergence, not a bare 'Session not found'."""
        from snodo.infrastructure.audit import AuditLog

        project = tmp_path / "proj"
        (project / ".snodo").mkdir(parents=True)
        audit = AuditLog(str(project / ".snodo" / "audit.log"))
        audit.append_event("session_started", {
            "op": "session_started",
            "session_id": "sess_20260101_prod_a1b2c3",
            "mode": "producer",
            "project_root": str(project),
        })

        args = SimpleNamespace(
            session_action="show", session_id="sess_20260101_prod_a1b2c3",
            sessions_dir=mgr.sessions_dir,
        )
        with patch("snodo.infrastructure.paths.require_project_root",
                   return_value=str(project)):
            result = session_command(args)
        assert result == 1
        err = capsys.readouterr().err
        assert "audit" in err
        assert "SNODO_HOME" in err

    def test_show_details(self, mgr, capsys):
        session = mgr.create_session("producer", PROJECT_ROOT)
        args = SimpleNamespace(
            session_action="show", session_id=session.session_id,
            sessions_dir=mgr.sessions_dir,
        )
        result = session_command(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "producer" in out


# ========== DELETE ==========

class TestSessionDelete:
    def test_delete_session(self, mgr, capsys):
        session = mgr.create_session("producer", PROJECT_ROOT)
        args = SimpleNamespace(
            session_action="delete", session_id=session.session_id,
            sessions_dir=mgr.sessions_dir,
        )
        result = session_command(args)
        assert result == 0
        # File should be gone
        with pytest.raises(FileNotFoundError):
            mgr.load_session(session.session_id)

    def test_delete_nonexistent(self, mgr, capsys):
        args = SimpleNamespace(
            session_action="delete", session_id="nonexistent",
            sessions_dir=mgr.sessions_dir,
        )
        result = session_command(args)
        assert result == 1


# ========== PRUNE ==========

class TestSessionPrune:
    def test_prune(self, mgr, capsys):
        from snodo.infrastructure.state import read_state, write_state

        session = mgr.create_session("producer", PROJECT_ROOT)
        # Backdate
        path = mgr.sessions_dir / f"{session.session_id}.json"
        data = json.loads(path.read_text())
        data["updated_at"] = (datetime.now(UTC) - timedelta(days=31)).isoformat()
        path.write_text(json.dumps(data, indent=2))
        # Clear active pointer (no-op for non-existent PROJECT_ROOT, but correct regardless)
        try:
            state = read_state(PROJECT_ROOT)
            state.active_session.pop("producer", None)
            write_state(PROJECT_ROOT, state)
        except (OSError, PermissionError):
            pass

        args = SimpleNamespace(
            session_action="prune", sessions_dir=mgr.sessions_dir,
        )
        with patch("snodo.config.ConfigManager") as mock_cm:
            mock_cm.return_value.get_engine_value.return_value = 30
            result = session_command(args)

        assert result == 0
        assert "1 stale" in capsys.readouterr().out

    def test_prune_nothing_to_prune(self, mgr, capsys):
        mgr.create_session("producer", PROJECT_ROOT)  # recent
        args = SimpleNamespace(
            session_action="prune", sessions_dir=mgr.sessions_dir,
        )
        with patch("snodo.config.ConfigManager") as mock_cm:
            mock_cm.return_value.get_engine_value.return_value = 30
            result = session_command(args)

        assert result == 0
        assert "0 stale" in capsys.readouterr().out

    def test_prune_days_overrides_config_max_age(self, mgr, capsys):
        """`session prune --days N` overrides the engine's max session age."""
        session = mgr.create_session("producer", PROJECT_ROOT)
        path = mgr.sessions_dir / f"{session.session_id}.json"
        data = json.loads(path.read_text())
        data["updated_at"] = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        path.write_text(json.dumps(data, indent=2))

        args = SimpleNamespace(
            session_action="prune", sessions_dir=mgr.sessions_dir, days=7,
        )
        # A generous config default would NOT prune a 10-day session; --days 7 does.
        with patch("snodo.config.ConfigManager") as mock_cm:
            mock_cm.return_value.get_engine_value.return_value = 999
            result = session_command(args)

        assert result == 0
        out = capsys.readouterr().out
        assert "1 stale" in out
        assert "max age: 7 days" in out

    def test_session_prune_flag_wires_days_to_args(self, monkeypatch):
        """The --days option is threaded onto the args the prune action reads,
        and is absent (None) when the flag is not given."""
        from typer.testing import CliRunner

        from snodo.cli.commands import session_cmd

        captured = {}
        monkeypatch.setattr(session_cmd, "SessionManager", lambda *a, **k: object())
        monkeypatch.setattr(
            session_cmd, "_session_prune",
            lambda mgr, args: captured.update(days=getattr(args, "days", "unset")) or 0,
        )
        runner = CliRunner()
        assert runner.invoke(session_cmd.app, ["prune", "--days", "7"]).exit_code == 0
        assert captured["days"] == 7
        assert runner.invoke(session_cmd.app, ["prune"]).exit_code == 0
        assert captured["days"] is None


# ========== FIXTURES FOR REAL PROJECT ==========

@pytest.fixture
def project_dir(tmp_path):
    p = tmp_path / "myproject"
    p.mkdir()
    (p / ".snodo").mkdir()
    (p / ".snodo" / "project.json").write_text(json.dumps({
        "id": "local:myproject_123",
        "project.id": "local:myproject_123",
        "scope": "local",
    }))
    (p / ".snodo" / "state.json").write_text(json.dumps({
        "current_mode": "producer",
        "active_session": {},
    }))
    return p


# ========== NEW ==========

class TestSessionNew:
    def test_new_session_when_none_exists(self, mgr, project_dir, capsys):
        args = SimpleNamespace(
            session_action="new",
            mode="producer",
            project_root=str(project_dir),
            sessions_dir=mgr.sessions_dir,
            yes=False,
        )
        result = session_command(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "Created new session: sess_" in out
        assert "(mode=producer)" in out

        active = mgr.get_active_session("producer", str(project_dir))
        assert active is not None
        assert active.session_id in out

    def test_new_session_leaves_previous_session_intact(self, mgr, project_dir, capsys):
        """Creating a new session makes it active, leaving previous session on disk unchanged."""
        s1 = mgr.create_session("producer", str(project_dir))
        mgr.update_decision(s1.session_id, "custom_decision", {"result": "preserved"})

        args = SimpleNamespace(
            session_action="new",
            mode="producer",
            project_root=str(project_dir),
            sessions_dir=mgr.sessions_dir,
            yes=True,
        )
        result = session_command(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "Created new session: sess_" in out

        active = mgr.get_active_session("producer", str(project_dir))
        assert active.session_id != s1.session_id

        # Previous session is still on disk with decisions intact
        loaded_s1 = mgr.load_session(s1.session_id)
        assert loaded_s1.checkpoint.decisions["custom_decision"] == {"result": "preserved"}

        # Previous session is still readable through session show
        show_args = SimpleNamespace(
            session_action="show",
            session_id=s1.session_id,
            sessions_dir=mgr.sessions_dir,
            json=False,
        )
        capsys.readouterr()
        show_res = session_command(show_args)
        assert show_res == 0
        show_out = capsys.readouterr().out
        assert s1.session_id in show_out
        assert "custom_decision" in show_out

        # Both sessions are listed
        list_args = SimpleNamespace(
            session_action="list",
            mode=None,
            project=str(project_dir),
            sessions_dir=mgr.sessions_dir,
        )
        list_res = session_command(list_args)
        assert list_res == 0
        list_out = capsys.readouterr().out
        assert s1.session_id in list_out
        assert active.session_id in list_out

    def test_new_session_warns_on_pending_proposals_and_cancels(self, mgr, project_dir, monkeypatch, capsys):
        """Warns when outgoing session has pending adjudication proposals, aborts on 'n'."""
        s1 = mgr.create_session("producer", str(project_dir))
        pending_data = {
            "task_abc": {
                "type": "adjudicate",
                "validator_id": "architecture",
                "decision": "proceed",
            }
        }
        mgr.update_decision(s1.session_id, "pending_decisions", pending_data)

        monkeypatch.setattr("builtins.input", lambda _: "n")

        args = SimpleNamespace(
            session_action="new",
            mode="producer",
            project_root=str(project_dir),
            sessions_dir=mgr.sessions_dir,
            yes=False,
        )
        result = session_command(args)
        assert result == 1
        captured = capsys.readouterr()
        assert "Warning: Active session" in captured.err
        assert "1 pending adjudication proposal(s)" in captured.err
        assert "task_abc" in captured.err
        assert "Cancelled: session not created" in captured.err

        # Active session remains s1
        active = mgr.get_active_session("producer", str(project_dir))
        assert active.session_id == s1.session_id

    def test_new_session_warns_on_pending_proposals_and_confirms(self, mgr, project_dir, monkeypatch, capsys):
        """Warns when outgoing session has pending proposals, proceeds on 'y'."""
        s1 = mgr.create_session("producer", str(project_dir))
        pending_data = {
            "task_xyz": {
                "type": "adjudicate",
                "validator_id": "security",
                "decision": "proceed",
            }
        }
        mgr.update_decision(s1.session_id, "pending_decisions", pending_data)

        monkeypatch.setattr("builtins.input", lambda _: "y")

        args = SimpleNamespace(
            session_action="new",
            mode="producer",
            project_root=str(project_dir),
            sessions_dir=mgr.sessions_dir,
            yes=False,
        )
        result = session_command(args)
        assert result == 0
        captured = capsys.readouterr()
        assert "Warning: Active session" in captured.err
        assert "task_xyz" in captured.err
        assert "Created new session: sess_" in captured.out

        # Active session is new session, s1 pending decisions unchanged
        active = mgr.get_active_session("producer", str(project_dir))
        assert active.session_id != s1.session_id
        loaded_s1 = mgr.load_session(s1.session_id)
        assert loaded_s1.checkpoint.decisions["pending_decisions"] == pending_data

    def test_new_session_warns_on_in_progress_task(self, mgr, project_dir, monkeypatch, capsys):
        """Warns when outgoing session has an in-progress task."""
        s1 = mgr.create_session("producer", str(project_dir))
        mgr.set_current_task(s1.session_id, "task_in_progress_123")

        monkeypatch.setattr("builtins.input", lambda _: "y")

        args = SimpleNamespace(
            session_action="new",
            mode="producer",
            project_root=str(project_dir),
            sessions_dir=mgr.sessions_dir,
            yes=False,
        )
        result = session_command(args)
        assert result == 0
        captured = capsys.readouterr()
        assert "In-progress task: task_in_progress_123" in captured.err
        assert "Created new session: sess_" in captured.out

    def test_new_session_yes_flag_skips_prompt(self, mgr, project_dir, capsys):
        """--yes / --force bypasses confirmation prompt even with live state."""
        s1 = mgr.create_session("producer", str(project_dir))
        mgr.set_current_task(s1.session_id, "task_123")
        mgr.update_decision(s1.session_id, "pending_decisions", {"t1": {"type": "adjudicate"}})

        args = SimpleNamespace(
            session_action="new",
            mode="producer",
            project_root=str(project_dir),
            sessions_dir=mgr.sessions_dir,
            yes=True,
        )
        result = session_command(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "Created new session: sess_" in out

        active = mgr.get_active_session("producer", str(project_dir))
        assert active.session_id != s1.session_id


# ========== SWITCH ==========

class TestSessionSwitch:
    def test_switch_session(self, mgr, project_dir, capsys):
        s1 = mgr.create_session("producer", str(project_dir))
        s2 = mgr.create_session("producer", str(project_dir))

        assert mgr.get_active_session("producer", str(project_dir)).session_id == s2.session_id

        args = SimpleNamespace(
            session_action="switch",
            session_id=s1.session_id,
            project_root=str(project_dir),
            sessions_dir=mgr.sessions_dir,
        )
        result = session_command(args)
        assert result == 0
        out = capsys.readouterr().out
        assert f"Switched to session: {s1.session_id}" in out
        assert mgr.get_active_session("producer", str(project_dir)).session_id == s1.session_id

    def test_switch_nonexistent(self, mgr, project_dir, capsys):
        args = SimpleNamespace(
            session_action="switch",
            session_id="sess_nonexistent",
            project_root=str(project_dir),
            sessions_dir=mgr.sessions_dir,
        )
        result = session_command(args)
        assert result == 1
        assert "not found" in capsys.readouterr().err.lower()


# ========== TYPER CLI RUNNER BINDINGS ==========

class TestSessionCliBindings:
    def test_session_new_cli_invocation(self, project_dir, monkeypatch):
        from typer.testing import CliRunner
        from snodo.cli.commands import session_cmd

        captured = {}
        monkeypatch.setattr(session_cmd, "SessionManager", lambda *a, **k: object())
        monkeypatch.setattr(
            session_cmd, "_session_new",
            lambda mgr, args: captured.update(
                mode=getattr(args, "mode", None),
                yes=getattr(args, "yes", False),
            ) or 0,
        )
        runner = CliRunner()
        res = runner.invoke(session_cmd.app, ["new", "--mode", "reviewer", "--yes"])
        assert res.exit_code == 0
        assert captured["mode"] == "reviewer"
        assert captured["yes"] is True

    def test_session_switch_cli_invocation(self, monkeypatch):
        from typer.testing import CliRunner
        from snodo.cli.commands import session_cmd

        captured = {}
        monkeypatch.setattr(session_cmd, "SessionManager", lambda *a, **k: object())
        monkeypatch.setattr(
            session_cmd, "_session_switch",
            lambda mgr, args: captured.update(
                session_id=getattr(args, "session_id", None),
            ) or 0,
        )
        runner = CliRunner()
        res = runner.invoke(session_cmd.app, ["switch", "sess_20260101_prod_abc123"])
        assert res.exit_code == 0
        assert captured["session_id"] == "sess_20260101_prod_abc123"


