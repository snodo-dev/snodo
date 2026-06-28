"""Tests for session state management and checkpointing (INV5).

Rewritten for 7.3 session model: (mode, project) scoped, no tokens,
global storage at ~/.snodo/sessions/ (configurable via sessions_dir).
"""

import json
import time
from datetime import datetime, UTC, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from snodo.infrastructure.session import (
    SessionManager, Checkpoint,
    _mode_prefix,
)
from snodo.project import get_project_id


# ========== FIXTURES ==========

@pytest.fixture
def sessions_dir(tmp_path):
    """Create a temp sessions directory."""
    d = tmp_path / "sessions"
    d.mkdir()
    return d


@pytest.fixture
def mgr(sessions_dir):
    """Create a SessionManager with temp dir."""
    return SessionManager(sessions_dir=sessions_dir)


@pytest.fixture
def audit_log():
    """Create a mock audit log."""
    log = MagicMock()
    log.append_event = MagicMock()
    return log


@pytest.fixture
def mgr_with_audit(sessions_dir, audit_log):
    """Create a SessionManager with audit log."""
    return SessionManager(audit_log=audit_log, sessions_dir=sessions_dir)


def _seed_project(path: Path, project_id: str) -> str:
    """Create a real project directory pre-seeded with a project.json cache."""
    path.mkdir(parents=True, exist_ok=True)
    snodo_dir = path / ".snodo"
    snodo_dir.mkdir()
    (snodo_dir / "project.json").write_text(json.dumps({
        "id": project_id,
        "project.id": project_id,
        "scope": "remote",
    }))
    return str(path)


@pytest.fixture
def project_root(tmp_path):
    """Real temp directory pre-seeded so get_project_id returns a stable ID."""
    return _seed_project(tmp_path / "project", "github.com/test/myproject")


@pytest.fixture
def project_root_alt(tmp_path):
    """Alternate real temp directory with a distinct stable project ID."""
    return _seed_project(tmp_path / "project_alt", "github.com/test/other")


# ========== SESSION ID FORMAT ==========

class TestSessionIdFormat:
    def test_mode_prefixes(self):
        assert _mode_prefix("producer") == "prod"
        assert _mode_prefix("reviewer") == "rev"
        assert _mode_prefix("planner") == "plan"
        assert _mode_prefix("unknown_mode") == "unkn"

    def test_project_id_is_canonical(self, mgr, project_root):
        """Session project_id matches the canonical get_project_id value."""
        session = mgr.create_session("producer", project_root)
        assert session.project_id == get_project_id(project_root)[0]

    def test_two_clones_same_remote_get_same_project_id(self, tmp_path):
        """Two filesystem paths sharing the same remote produce the same project_id."""
        pid = "github.com/org/repo"
        clone_a = _seed_project(tmp_path / "clone_a", pid)
        clone_b = _seed_project(tmp_path / "clone_b", pid)
        assert get_project_id(clone_a)[0] == pid
        assert get_project_id(clone_b)[0] == pid

    def test_no_remote_produces_stable_local_id(self, tmp_path):
        """A project with no git remote gets a stable local:-prefixed id via cache."""
        root = str(tmp_path / "no_remote_project")
        Path(root).mkdir()
        pid_1, scope_1 = get_project_id(root)
        pid_2, scope_2 = get_project_id(root)
        assert pid_1 == pid_2
        assert pid_1.startswith("local:")
        assert scope_1 == "local"

    def test_session_id_format(self, mgr, project_root):
        session = mgr.create_session("producer", project_root)
        parts = session.session_id.split("_")
        assert parts[0] == "sess"
        assert len(parts[1]) == 8  # YYYYMMDD
        assert parts[2] == "prod"
        assert len(parts[3]) == 6  # random hex

    def test_session_id_unique(self, mgr, project_root):
        s1 = mgr.create_session("producer", project_root)
        s2 = mgr.create_session("producer", project_root)
        assert s1.session_id != s2.session_id


# ========== CREATE SESSION ==========

class TestCreateSession:
    def test_create_basic(self, mgr, project_root):
        session = mgr.create_session("producer", project_root)
        assert session.mode == "producer"
        assert session.project_root == project_root
        assert session.project_id == get_project_id(project_root)[0]
        assert session.created_at is not None
        assert session.updated_at is not None

    def test_create_persists_to_disk(self, mgr, sessions_dir, project_root):
        session = mgr.create_session("producer", project_root)
        path = sessions_dir / f"{session.session_id}.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["mode"] == "producer"

    def test_checkpoint_defaults(self, mgr, project_root):
        session = mgr.create_session("producer", project_root)
        assert session.checkpoint.current_task is None
        assert session.checkpoint.decisions == {}
        assert session.checkpoint.memory_summary == ""
        assert session.checkpoint.timestamp != ""

    def test_no_tokens_in_session(self, mgr, project_root):
        """INV5: tokens deliberately excluded from session state."""
        session = mgr.create_session("producer", project_root)
        assert not hasattr(session, "tokens")
        data = json.loads(
            (mgr.sessions_dir / f"{session.session_id}.json").read_text()
        )
        assert "tokens" not in data

    def test_no_task_graph_in_session(self, mgr, project_root):
        """task_graph removed from new session model."""
        session = mgr.create_session("producer", project_root)
        assert not hasattr(session, "task_graph")


# ========== GET ACTIVE SESSION ==========

class TestGetActiveSession:
    def test_no_active_returns_none(self, mgr, project_root):
        assert mgr.get_active_session("producer", project_root) is None

    def test_finds_active(self, mgr, project_root):
        created = mgr.create_session("producer", project_root)
        found = mgr.get_active_session("producer", project_root)
        assert found is not None
        assert found.session_id == created.session_id

    def test_mode_mismatch(self, mgr, project_root):
        mgr.create_session("producer", project_root)
        assert mgr.get_active_session("reviewer", project_root) is None

    def test_project_mismatch(self, mgr, project_root, project_root_alt):
        mgr.create_session("producer", project_root)
        assert mgr.get_active_session("producer", project_root_alt) is None

    def test_closed_not_returned(self, mgr, project_root):
        session = mgr.create_session("producer", project_root)
        mgr.delete_session(session.session_id)
        assert mgr.get_active_session("producer", project_root) is None


# ========== LOAD SESSION ==========

class TestLoadSession:
    def test_load_existing(self, mgr, project_root):
        created = mgr.create_session("producer", project_root)
        loaded = mgr.load_session(created.session_id)
        assert loaded.session_id == created.session_id
        assert loaded.mode == "producer"

    def test_load_nonexistent_raises(self, mgr):
        with pytest.raises(FileNotFoundError, match="No session found"):
            mgr.load_session("nonexistent")

    def test_roundtrip_preserves_all_fields(self, mgr, project_root):
        session = mgr.create_session("planner", project_root)
        mgr.update_decision(session.session_id, "auto_approve", True)
        mgr.update_memory_summary(session.session_id, "context about the project")
        mgr.set_current_task(session.session_id, "task_001")

        loaded = mgr.load_session(session.session_id)
        assert loaded.mode == "planner"
        assert loaded.checkpoint.decisions == {"auto_approve": True}
        assert loaded.checkpoint.memory_summary == "context about the project"
        assert loaded.checkpoint.current_task == "task_001"


# ========== SAVE CHECKPOINT ==========

class TestSaveCheckpoint:
    def test_save_updates_timestamp(self, mgr, project_root):
        session = mgr.create_session("producer", project_root)
        original = session.checkpoint.timestamp
        time.sleep(0.01)
        mgr.save_checkpoint(session.session_id)
        loaded = mgr.load_session(session.session_id)
        assert loaded.checkpoint.timestamp > original

    def test_save_with_checkpoint_data(self, mgr, project_root):
        session = mgr.create_session("producer", project_root)
        cp = Checkpoint(current_task="t1", decisions={"k": "v"}, memory_summary="sum")
        mgr.save_checkpoint(session.session_id, checkpoint=cp)
        loaded = mgr.load_session(session.session_id)
        assert loaded.checkpoint.current_task == "t1"
        assert loaded.checkpoint.decisions == {"k": "v"}
        assert loaded.checkpoint.memory_summary == "sum"


# ========== UPDATE DECISION ==========

class TestUpdateDecision:
    def test_add_decision(self, mgr, project_root):
        session = mgr.create_session("producer", project_root)
        mgr.update_decision(session.session_id, "auto_approve_subtasks", False)
        loaded = mgr.load_session(session.session_id)
        assert loaded.checkpoint.decisions["auto_approve_subtasks"] is False

    def test_overwrite_decision(self, mgr, project_root):
        session = mgr.create_session("producer", project_root)
        mgr.update_decision(session.session_id, "key", "old")
        mgr.update_decision(session.session_id, "key", "new")
        loaded = mgr.load_session(session.session_id)
        assert loaded.checkpoint.decisions["key"] == "new"

    def test_decision_persists_across_tasks(self, mgr, project_root):
        """Decision survives across tasks within same session."""
        session = mgr.create_session("producer", project_root)
        mgr.update_decision(session.session_id, "auto_approve", True)
        mgr.set_current_task(session.session_id, "task_002")  # new task
        loaded = mgr.load_session(session.session_id)
        assert loaded.checkpoint.decisions["auto_approve"] is True

    def test_decision_does_not_cross_sessions(self, mgr, project_root, project_root_alt):
        """Decisions are per-session, not global."""
        s1 = mgr.create_session("producer", project_root)
        mgr.update_decision(s1.session_id, "key", "val")
        s2 = mgr.create_session("producer", project_root_alt)
        loaded = mgr.load_session(s2.session_id)
        assert loaded.checkpoint.decisions == {}


# ========== UPDATE MEMORY SUMMARY ==========

class TestUpdateMemorySummary:
    def test_update_summary(self, mgr, project_root):
        session = mgr.create_session("producer", project_root)
        mgr.update_memory_summary(session.session_id, "project uses Flask")
        loaded = mgr.load_session(session.session_id)
        assert loaded.checkpoint.memory_summary == "project uses Flask"


# ========== SET CURRENT TASK ==========

class TestSetCurrentTask:
    def test_set_task(self, mgr, project_root):
        session = mgr.create_session("producer", project_root)
        mgr.set_current_task(session.session_id, "task_001")
        loaded = mgr.load_session(session.session_id)
        assert loaded.checkpoint.current_task == "task_001"

    def test_clear_task(self, mgr, project_root):
        session = mgr.create_session("producer", project_root)
        mgr.set_current_task(session.session_id, "task_001")
        mgr.set_current_task(session.session_id, None)
        loaded = mgr.load_session(session.session_id)
        assert loaded.checkpoint.current_task is None


# ========== DELETE SESSION ==========

class TestDeleteSession:
    def test_delete_removes_session(self, mgr, project_root):
        session = mgr.create_session("producer", project_root)
        mgr.delete_session(session.session_id)
        with pytest.raises(FileNotFoundError):
            mgr.load_session(session.session_id)

    def test_delete_nonexistent_raises(self, mgr):
        with pytest.raises(FileNotFoundError):
            mgr.delete_session("nonexistent")


# ========== LIST SESSIONS ==========

class TestListSessions:
    def test_list_empty(self, mgr):
        assert mgr.list_sessions() == []

    def test_list_all(self, mgr, project_root):
        mgr.create_session("producer", project_root)
        mgr.create_session("reviewer", project_root)
        assert len(mgr.list_sessions()) == 2

    def test_filter_by_mode(self, mgr, project_root):
        mgr.create_session("producer", project_root)
        mgr.create_session("reviewer", project_root)
        result = mgr.list_sessions(mode="producer")
        assert len(result) == 1
        assert result[0].mode == "producer"

    def test_filter_by_project(self, mgr, project_root, project_root_alt):
        mgr.create_session("producer", project_root)
        mgr.create_session("producer", project_root_alt)
        result = mgr.list_sessions(project_root=project_root)
        assert len(result) == 1

    def test_filter_by_status(self, mgr, project_root, project_root_alt):
        s1 = mgr.create_session("producer", project_root)
        mgr.create_session("producer", project_root_alt)
        mgr.delete_session(s1.session_id)
        result = mgr.list_sessions(status="active")
        assert len(result) == 1


# ========== PRUNE STALE ==========

class TestPruneStale:
    def test_prune_old_sessions(self, mgr, sessions_dir, project_root):
        from snodo.infrastructure.state import read_state, write_state

        session = mgr.create_session("producer", project_root)
        # Backdate the session file
        path = sessions_dir / f"{session.session_id}.json"
        data = json.loads(path.read_text())
        old_time = (datetime.now(UTC) - timedelta(days=31)).isoformat()
        data["updated_at"] = old_time
        path.write_text(json.dumps(data, indent=2))
        # Clear active pointer so the session is not protected from pruning
        state = read_state(project_root)
        state.active_session.pop("producer", None)
        write_state(project_root, state)

        count = mgr.prune_stale(max_age_days=30)
        assert count == 1
        # File should be deleted — loading raises FileNotFoundError
        with pytest.raises(FileNotFoundError):
            mgr.load_session(session.session_id)

    def test_prune_keeps_recent(self, mgr, project_root):
        mgr.create_session("producer", project_root)
        count = mgr.prune_stale(max_age_days=30)
        assert count == 0


# ========== AUDIT LOG ==========

class TestAuditLog:
    def test_create_session_audits(self, mgr_with_audit, audit_log, project_root):
        mgr_with_audit.create_session("producer", project_root)
        audit_log.append_event.assert_called()
        call_args = audit_log.append_event.call_args
        assert call_args[0][0] == "session_started"
        assert call_args[0][1]["mode"] == "producer"

    def test_delete_session_audits(self, mgr_with_audit, audit_log, project_root):
        session = mgr_with_audit.create_session("producer", project_root)
        audit_log.reset_mock()
        mgr_with_audit.delete_session(session.session_id)
        audit_log.append_event.assert_called_once()
        call_args = audit_log.append_event.call_args
        assert call_args[0][0] == "session_deleted"

    def test_update_decision_audits(self, mgr_with_audit, audit_log, project_root):
        session = mgr_with_audit.create_session("producer", project_root)
        audit_log.reset_mock()
        mgr_with_audit.update_decision(session.session_id, "key", "val")
        audit_log.append_event.assert_called_once()
        assert audit_log.append_event.call_args[0][0] == "session_decision_updated"

    def test_update_memory_audits(self, mgr_with_audit, audit_log, project_root):
        session = mgr_with_audit.create_session("producer", project_root)
        audit_log.reset_mock()
        mgr_with_audit.update_memory_summary(session.session_id, "text")
        audit_log.append_event.assert_called_once()
        assert audit_log.append_event.call_args[0][0] == "session_memory_updated"

    def test_set_task_audits(self, mgr_with_audit, audit_log, project_root):
        session = mgr_with_audit.create_session("producer", project_root)
        audit_log.reset_mock()
        mgr_with_audit.set_current_task(session.session_id, "t1")
        audit_log.append_event.assert_called_once()
        data = audit_log.append_event.call_args[0][1]
        assert data["new_task"] == "t1"

    def test_no_audit_log_no_error(self, sessions_dir, project_root):
        """SessionManager works without audit_log."""
        mgr = SessionManager(sessions_dir=sessions_dir)
        session = mgr.create_session("producer", project_root)
        mgr.delete_session(session.session_id)  # should not raise

    def test_prune_audits(self, mgr_with_audit, audit_log, sessions_dir, project_root):
        from snodo.infrastructure.state import read_state, write_state

        session = mgr_with_audit.create_session("producer", project_root)
        # Backdate
        path = sessions_dir / f"{session.session_id}.json"
        data = json.loads(path.read_text())
        old_time = (datetime.now(UTC) - timedelta(days=31)).isoformat()
        data["updated_at"] = old_time
        path.write_text(json.dumps(data, indent=2))
        # Clear active pointer so the session is not protected from pruning
        state = read_state(project_root)
        state.active_session.pop("producer", None)
        write_state(project_root, state)

        audit_log.reset_mock()
        mgr_with_audit.prune_stale(max_age_days=30)
        assert any(
            c[0][0] == "session_deleted"
            for c in audit_log.append_event.call_args_list
        )


# ========== MULTI-PROCESS ==========

class TestMultiProcess:
    def test_process_a_creates_process_b_sees(self, sessions_dir, project_root):
        """Simulate multi-process: process A creates, process B reads."""
        mgr_a = SessionManager(sessions_dir=sessions_dir)
        session = mgr_a.create_session("producer", project_root)

        mgr_b = SessionManager(sessions_dir=sessions_dir)
        found = mgr_b.get_active_session("producer", project_root)
        assert found is not None
        assert found.session_id == session.session_id


# ========== INV5 INTEGRATION ==========

class TestINV5:
    def test_resume_preserves_decisions_and_memory(self, mgr, project_root):
        """INV5: resume preserves decisions + memory_summary."""
        session = mgr.create_session("producer", project_root)
        mgr.update_decision(session.session_id, "auto_approve", True)
        mgr.update_memory_summary(session.session_id, "project uses Flask and PostgreSQL")
        mgr.save_checkpoint(session.session_id)

        # Simulate process restart
        mgr2 = SessionManager(sessions_dir=mgr.sessions_dir)
        loaded = mgr2.load_session(session.session_id)
        assert loaded.checkpoint.decisions == {"auto_approve": True}
        assert loaded.checkpoint.memory_summary == "project uses Flask and PostgreSQL"

    def test_tokens_not_in_checkpoint(self, mgr, project_root):
        """INV5: tokens are NOT persisted in checkpoint."""
        session = mgr.create_session("producer", project_root)
        mgr.save_checkpoint(session.session_id)
        path = mgr.sessions_dir / f"{session.session_id}.json"
        data = json.loads(path.read_text())
        assert "tokens" not in data
        assert "tokens" not in data.get("checkpoint", {})
