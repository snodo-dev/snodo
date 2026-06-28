"""Tests for session CLI commands (snodo session list/show/delete/prune).

FILE: tests/cli/test_session_cmd.py
"""

import json
from datetime import datetime, UTC, timedelta
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
