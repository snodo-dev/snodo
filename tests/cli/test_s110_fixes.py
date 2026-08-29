"""Tests for S110 try-except-pass fixes in snodo/ (Fixes #125).

Covers the behaviour changes: audit writes that fail now log a warning
instead of failing silently, and the task-abandon failure-context clear now
logs instead of swallowing.
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from snodo.cli.commands.install_cmd import _audit_global
from snodo.cli.commands.task_cmd import task_abandon_command


def test_audit_global_logs_warning_on_failure(caplog):
    """A failed audit append is logged, not silently dropped (Fixes #125)."""
    with patch("snodo.infrastructure.paths.resolve_home", side_effect=OSError("no home")):
        with caplog.at_level(logging.WARNING, logger="snodo.cli.commands.install_cmd"):
            _audit_global("install_registered", {"entry_name": "x"})

    assert any(
        "Could not record audit event install_registered" in r.message
        for r in caplog.records
    )


def test_audit_global_success_no_warning(caplog, tmp_path, monkeypatch):
    """A successful audit append produces no warning."""
    from snodo.infrastructure.audit import AuditLog

    log_path = tmp_path / "audit.log"
    monkeypatch.setattr(
        "snodo.infrastructure.paths.resolve_home",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "snodo.infrastructure.audit.AuditLog",
        lambda p, project_id="": AuditLog(str(log_path)),
    )

    with caplog.at_level(logging.WARNING, logger="snodo.cli.commands.install_cmd"):
        _audit_global("install_registered", {"entry_name": "x"})

    assert not any(
        "Could not record audit event" in r.message for r in caplog.records
    )
    events = AuditLog(str(log_path)).get_history("install_registered")
    assert len(events) == 1


def test_task_abandon_logs_failure_context_clear_error(tmp_path, monkeypatch, caplog):
    """task_abandon logs when it cannot clear the failure context (Fixes #125)."""
    from pathlib import Path
    from snodo.infrastructure.session import SessionManager
    from snodo.infrastructure.state import ProjectState, write_state

    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))

    sessions_dir = Path(tmp_path) / ".snodo" / "sessions"
    mgr = SessionManager(sessions_dir=sessions_dir)
    session = mgr.create_session("dev", str(tmp_path))
    write_state(str(tmp_path), ProjectState(current_mode="dev", active_session={"dev": session.session_id}))
    monkeypatch.setattr("snodo.infrastructure.session.SessionManager", lambda *a, **kw: mgr)

    mgr.update_decision(session.session_id, "task_failure", {"t1": {"branch": "task/t1"}})

    # Force update_decision to fail.
    monkeypatch.setattr(mgr, "update_decision", MagicMock(side_effect=OSError("db locked")))

    # Git branch deletion and worktree removal are best-effort; stub them.
    mock_git = MagicMock()
    mock_head = MagicMock()
    mock_head.name = "task/t1"
    mock_git.repo.heads = [mock_head]
    monkeypatch.setattr("snodo.tools.git.GitMCP", lambda p: mock_git)
    monkeypatch.setattr("snodo.infrastructure.worktree.remove_worktree", lambda p, t: None)

    with caplog.at_level(logging.WARNING, logger="snodo.cli.commands.task_cmd"):
        res = task_abandon_command(SimpleNamespace(task_id="t1"))

    assert res == 0
    assert any(
        "Could not clear failure context for task t1" in r.message
        for r in caplog.records
    )
