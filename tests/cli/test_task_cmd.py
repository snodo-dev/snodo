"""Tests for snodo task commands (list, show, abandon, prune, review, report).

FILE: tests/cli/test_task_cmd.py
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from snodo.infrastructure.audit import AuditLog
from snodo.infrastructure.session import SessionManager
from snodo.infrastructure.state import ProjectState, write_state

from snodo.cli.commands.task_cmd import (
    _get_all_task_branches,
    _merge_identity,
    _task_callback,
    task_abandon,
    task_abandon_command,
    task_list,
    task_list_command,
    task_prune,
    task_prune_command,
    task_report,
    task_report_command,
    task_review,
    task_review_command,
    task_show,
    task_show_command,
)


def _setup_project_with_session(tmp_path, mode="dev", monkeypatch=None):
    """Helper to set up .snodo/state.json and an active session."""
    sessions_dir = Path(tmp_path) / ".snodo" / "sessions"
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    mgr = SessionManager(audit_log=audit_log, sessions_dir=sessions_dir)
    session = mgr.create_session(mode, str(tmp_path))

    state = ProjectState(current_mode=mode, active_session={mode: session.session_id})
    write_state(str(tmp_path), state)

    if monkeypatch:
        monkeypatch.setattr("snodo.infrastructure.session.SessionManager", lambda *a, **kw: mgr)

    return mgr, session


# ============================================================================
# 1. _merge_identity helper tests
# ============================================================================

def test_merge_identity_resolution():
    """_merge_identity prefers merge_sha over task_ref and task_id."""
    # 1. merge_sha takes priority
    assert _merge_identity({"merge_sha": "sha123", "task_ref": "ref456", "task_id": "id789"}) == "sha123"
    # 2. Fallback to task_ref
    assert _merge_identity({"task_ref": "ref456", "task_id": "id789"}) == "ref456"
    # 3. Fallback to task_id
    assert _merge_identity({"task_id": "id789"}) == "id789"
    # 4. Empty fallback
    assert _merge_identity({}) == ""


# ============================================================================
# 2. task_report_command tests
# ============================================================================

def test_task_report_independent_denominators(tmp_path, monkeypatch, capsys):
    """completed_tasks and merged_units are counted independently (e.g. 43 vs 31)."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    # 3 completed tasks
    audit_log.append_event("task_complete", {"op": "task_complete", "task_ref": "t1"})
    audit_log.append_event("task_complete", {"op": "task_complete", "task_ref": "t2"})
    audit_log.append_event("task_complete", {"op": "task_complete", "task_ref": "t3"})

    # 2 merged units (different count)
    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t1", "merge_sha": "sha_a"})
    audit_log.append_event("human_review_recorded", {"op": "human_review_recorded", "merge_sha": "sha_a", "verdict": "accepted"})
    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t2", "merge_sha": "sha_b"})

    args = SimpleNamespace(days=30, json=True)
    res = task_report_command(args)
    assert res == 0

    data = json.loads(capsys.readouterr().out)
    assert data["completed_tasks"] == 3
    assert data["merged_units"] == 2
    assert data["total_reviewed"] == 1
    assert data["accepted_unchanged"] == 1
    assert data["unreviewed"] == 1


def test_task_report_days_window_and_unparseable_timestamps(tmp_path, monkeypatch, capsys):
    """Events outside days_window are cut off; missing or invalid timestamps are retained."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    now = datetime.now(timezone.utc)
    old_time = (now - timedelta(days=60)).isoformat()

    # Old event (outside 30-day window)
    audit_log.append_event("task_complete", {"op": "task_complete", "task_ref": "t_old", "timestamp": old_time})
    # Valid recent event
    audit_log.append_event("task_complete", {"op": "task_complete", "task_ref": "t_new", "timestamp": now.isoformat()})
    # Event with missing timestamp
    audit_log.append_event("task_complete", {"op": "task_complete", "task_ref": "t_no_ts"})
    # Event with invalid timestamp string
    audit_log.append_event("task_complete", {"op": "task_complete", "task_ref": "t_bad_ts", "timestamp": "invalid_date_string"})

    args = SimpleNamespace(days=30, json=True)
    res = task_report_command(args)
    assert res == 0

    data = json.loads(capsys.readouterr().out)
    assert data["completed_tasks"] == 3  # t_new, t_no_ts, t_bad_ts (t_old cut off)


def test_task_report_zero_reviewed_has_no_acceptance_rate(tmp_path, monkeypatch, capsys):
    """When total_reviewed is 0, acceptance_rate_pct reports no rate (null),
    not 0.0% — an empty denominator is not a zero-percent acceptance."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t1", "merge_sha": "sha1"})

    args = SimpleNamespace(days=30, json=True)
    res = task_report_command(args)
    assert res == 0

    data = json.loads(capsys.readouterr().out)
    assert data["total_reviewed"] == 0
    assert data["acceptance_rate_pct"] is None


def test_task_report_outside_project_root(tmp_path, monkeypatch, capsys):
    """task_report_command returns 1 when not inside a snodo project."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: None)

    # Text output
    res = task_report_command(SimpleNamespace(days=30, json=False))
    assert res == 1
    assert "Not inside a snodo project." in capsys.readouterr().err

    # JSON output
    res = task_report_command(SimpleNamespace(days=30, json=True))
    assert res == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False


def test_task_report_text_formatting(tmp_path, monkeypatch, capsys):
    """task_report_command text mode outputs human-readable summary."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    args = SimpleNamespace(days=7, json=False)
    res = task_report_command(args)
    assert res == 0

    out = capsys.readouterr().out
    assert "Human Review Acceptance Rate (Last 7 days)" in out
    assert "Completed tasks (task_complete): 0" in out


# ============================================================================
# 3. task_review_command tests
# ============================================================================

def test_task_review_missing_verdict_message(tmp_path, monkeypatch, capsys):
    """Missing verdict prints 'A verdict is required' (Fixes CLI gap)."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))

    args = SimpleNamespace(task_id="task_123", verdict=None, notes=None, report=False, days=30, json=False)
    res = task_review_command(args)
    assert res == 1

    err = capsys.readouterr().err
    assert "Error: A verdict is required. Must be one of: accepted, amended, discarded" in err


def test_task_review_missing_verdict_json(tmp_path, monkeypatch, capsys):
    """Missing verdict with --json returns error payload."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))

    args = SimpleNamespace(task_id="task_123", verdict=None, notes=None, report=False, days=30, json=True)
    res = task_review_command(args)
    assert res == 1

    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    assert "A verdict is required" in data["error"]


def test_task_review_missing_task_id(tmp_path, monkeypatch, capsys):
    """Missing task_id prints error and returns 1."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))

    # Text mode
    res = task_review_command(SimpleNamespace(task_id="", verdict="accepted", report=False, json=False))
    assert res == 1
    assert "Usage: snodo task review" in capsys.readouterr().err

    # JSON mode
    res = task_review_command(SimpleNamespace(task_id="", verdict="accepted", report=False, json=True))
    assert res == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False


def test_task_review_all_valid_verdicts(tmp_path, monkeypatch):
    """Valid verdicts ('accepted', 'amended', 'discarded') are recorded."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    for verdict in ["accepted", "AMENDED", "discarded"]:
        args = SimpleNamespace(task_id="t1", verdict=verdict, notes="note", report=False, days=30, json=False)
        assert task_review_command(args) == 0

    events = audit_log.get_history("human_review_recorded")
    assert len(events) == 3
    assert events[0].data["verdict"] == "accepted"
    assert events[1].data["verdict"] == "amended"
    assert events[2].data["verdict"] == "discarded"


def test_task_review_latest_verdict_wins(tmp_path, monkeypatch, capsys):
    """Re-reviewing the same identity overwrites prior verdict (latest wins)."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "sha1", "merge_sha": "sha1"})

    # 1. Initial review: discarded
    task_review_command(SimpleNamespace(task_id="sha1", verdict="discarded", notes="", report=False, days=30, json=False))
    # 2. Re-review: accepted (latest wins)
    task_review_command(SimpleNamespace(task_id="sha1", verdict="accepted", notes="", report=False, days=30, json=False))

    capsys.readouterr()  # Clear stdout text from review commands

    task_report_command(SimpleNamespace(days=30, json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["accepted_unchanged"] == 1
    assert data["discarded"] == 0


def test_task_review_delegates_to_report(tmp_path, monkeypatch, capsys):
    """task review 'report' or --report delegates to task_report_command."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    res = task_review_command(SimpleNamespace(task_id="report", verdict=None, notes=None, report=False, days=30, json=True))
    assert res == 0
    data = json.loads(capsys.readouterr().out)
    assert data["schema"] == "snodo.task_review_report.v1"


def test_task_review_outside_project_root_or_missing_audit_log(tmp_path, monkeypatch, capsys):
    """task_review returns error when outside project root or audit log is missing."""
    # Outside project root
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: None)
    res = task_review_command(SimpleNamespace(task_id="t1", verdict="accepted", notes="", report=False, json=True))
    assert res == 1

    # Missing audit log
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: None)
    res = task_review_command(SimpleNamespace(task_id="t1", verdict="accepted", notes="", report=False, json=False))
    assert res == 1
    assert "Audit log unavailable" in capsys.readouterr().err


# ============================================================================
# 4. task_list_command tests
# ============================================================================

def test_task_list_outside_project(tmp_path, monkeypatch, capsys):
    """task_list_command returns 1 when not inside a snodo project."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: None)
    res = task_list_command(SimpleNamespace())
    assert res == 1
    assert "Not inside a snodo project." in capsys.readouterr().err


def test_task_list_empty_failures(tmp_path, monkeypatch, capsys):
    """task_list_command returns 0 when no task branches in session."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)

    res = task_list_command(SimpleNamespace())
    assert res == 0
    assert "No task branches in current session." in capsys.readouterr().out


def test_task_list_shows_branches(tmp_path, monkeypatch, capsys):
    """task_list_command displays task failure table."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    mgr, session = _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)

    task_failures = {
        "t1": {"branch": "task/t1", "attempt": 1},
    }
    mgr.update_decision(session.session_id, "task_failure", task_failures)

    res = task_list_command(SimpleNamespace())
    assert res == 0
    out = capsys.readouterr().out
    assert "TASK ID" in out
    assert "t1" in out
    assert "task/t1" in out


def test_task_list_shows_all_tasks_and_honest_statuses(tmp_path, monkeypatch, capsys):
    """task_list_command displays all tasks from session records and git branches with honest status."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    mgr, session = _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)

    mgr.update_decision(session.session_id, "halt", {
        "t_blocked": {
            "status": "blocked",
            "halt_type": "blocker",
            "final_decision": "blocker",
            "raw_halt_type": "blocker",
            "task_id": "t_blocked",
            "phase": "post_execute",
        },
        "t_completed": {
            "status": "completed",
            "halt_type": "completed",
            "final_decision": "completed",
            "task_id": "t_completed",
            "phase": "complete",
        },
        "t_merged": {
            "status": "completed",
            "halt_type": "completed",
            "final_decision": "completed",
            "task_id": "t_merged",
            "phase": "complete",
        }
    })

    from snodo.infrastructure.audit import get_audit_log
    audit_path = tmp_path / ".snodo" / "audit.log"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit = get_audit_log(str(audit_path))
    audit.append_event("task_merged", {"op": "task_merged", "task_ref": "t_merged", "branch": "task/t_merged"})

    mock_git = MagicMock()
    b1 = MagicMock()
    b1.name = "task/t_completed/add-feature"
    b1.commit.committed_date = 1000000
    b2 = MagicMock()
    b2.name = "task/t_in_progress/work"
    b2.commit.committed_date = 1000000
    mock_git.repo.heads = [b1, b2]
    monkeypatch.setattr("snodo.tools.git.GitMCP", lambda p: mock_git)

    res = task_list_command(SimpleNamespace())
    assert res == 0
    out = capsys.readouterr().out

    lines = [line.strip() for line in out.splitlines() if line.strip()]
    task_statuses = {}
    for line in lines:
        parts = line.split()
        if len(parts) >= 4 and parts[0].startswith("t_"):
            task_statuses[parts[0]] = parts[3]

    assert task_statuses.get("t_blocked") == "failed"
    assert task_statuses.get("t_merged") == "merged"
    assert task_statuses.get("t_completed") == "completed"
    assert task_statuses.get("t_in_progress") == "in_progress"


# ============================================================================
# 5. task_show_command tests
# ============================================================================

def test_task_show_missing_task_id(tmp_path, monkeypatch, capsys):
    """task_show_command requires task_id."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))

    # Text mode
    res = task_show_command(SimpleNamespace(task_id="", json=False))
    assert res == 1
    assert "Usage: snodo task show" in capsys.readouterr().err

    # JSON mode
    res = task_show_command(SimpleNamespace(task_id="", json=True))
    assert res == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False


def test_task_show_no_active_mode_or_session(tmp_path, monkeypatch, capsys):
    """task_show_command returns error when mode or session missing."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))

    # Outside project
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: None)
    res = task_show_command(SimpleNamespace(task_id="t1", json=True))
    assert res == 1
    data = json.loads(capsys.readouterr().out)
    assert "Not inside a snodo project" in data["error"]

    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))

    # No active mode
    write_state(str(tmp_path), ProjectState(current_mode=""))
    res = task_show_command(SimpleNamespace(task_id="t1", json=True))
    assert res == 1
    data = json.loads(capsys.readouterr().out)
    assert "No active mode" in data["error"]

    # Active mode but missing session
    write_state(str(tmp_path), ProjectState(current_mode="dev"))
    monkeypatch.setattr("snodo.infrastructure.session.SessionManager.get_active_session", lambda self, m, p: None)
    res = task_show_command(SimpleNamespace(task_id="t1", json=False))
    assert res == 1
    assert "No active session for mode=dev" in capsys.readouterr().err


def test_task_show_no_record_for_task(tmp_path, monkeypatch, capsys):
    """task_show_command returns 1 when task_id not found in session."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)

    res = task_show_command(SimpleNamespace(task_id="nonexistent", json=False))
    assert res == 1
    assert "No record for task nonexistent" in capsys.readouterr().out

    res = task_show_command(SimpleNamespace(task_id="nonexistent", json=True))
    assert res == 1
    data = json.loads(capsys.readouterr().out)
    assert "No record for task" in data["error"]


def test_task_show_displays_halt_and_failure(tmp_path, monkeypatch, capsys):
    """task_show_command outputs halt and failure context in text and json."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    mgr, session = _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)

    halt_data = {
        "t1": {
            "final_decision": "halt",
            "halt_type": "blocked",
            "phase": "post_execute",
            "reason": "quality failed",
            "hint": "check tests",
            "validator_results": [{"validator_id": "quality", "severity": "blocker", "justification": "failed"}],
        }
    }
    failure_data = {
        "t1": {
            "attempt": 2,
            "branch": "task/t1",
            "files_changed": ["src/app.py"],
        }
    }
    mgr.update_decision(session.session_id, "halt", halt_data)
    mgr.update_decision(session.session_id, "task_failure", failure_data)

    # Text mode
    res = task_show_command(SimpleNamespace(task_id="t1", json=False))
    assert res == 0
    out = capsys.readouterr().out
    assert "Task:    t1" in out
    assert "final_decision: halt" in out
    assert "reason:         quality failed" in out
    assert "hint:           check tests" in out
    assert "quality [blocker]: failed" in out
    assert "branch:  task/t1" in out
    assert "files:   src/app.py" in out

    # JSON mode
    res = task_show_command(SimpleNamespace(task_id="t1", json=True))
    assert res == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["halt"]["halt_type"] == "blocked"
    assert data["failure"]["attempt"] == 2
    assert data["spec"] is None


def test_task_show_spec_from_halt_only_record(tmp_path, monkeypatch, capsys):
    """The spec renders from a halt-only record (Fixes #117)."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    mgr, session = _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)

    mgr.update_decision(session.session_id, "halt", {
        "t1": {
            "final_decision": "halt",
            "halt_type": "blocked",
            "phase": "pre_execute",
            "task_spec": "Implement the card footer per docs/design/card-footer-qr.html",
            "validator_results": [],
        }
    })

    res = task_show_command(SimpleNamespace(task_id="t1", json=False))
    assert res == 0
    out = capsys.readouterr().out
    assert "Task spec:" in out
    assert "Implement the card footer per docs/design/card-footer-qr.html" in out

    res = task_show_command(SimpleNamespace(task_id="t1", json=True))
    assert res == 0
    data = json.loads(capsys.readouterr().out)
    assert data["spec"] == "Implement the card footer per docs/design/card-footer-qr.html"


def test_task_show_spec_from_task_failure_preferred(tmp_path, monkeypatch, capsys):
    """task_failure's spec is preferred when both records carry one (Fixes #117)."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    mgr, session = _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)

    mgr.update_decision(session.session_id, "halt", {
        "t1": {
            "final_decision": "halt",
            "halt_type": "blocked",
            "phase": "post_execute",
            "task_spec": "halt spec",
            "validator_results": [],
        }
    })
    mgr.update_decision(session.session_id, "task_failure", {
        "t1": {
            "attempt": 1,
            "branch": "task/t1",
            "spec": "failure spec",
        }
    })

    res = task_show_command(SimpleNamespace(task_id="t1", json=False))
    assert res == 0
    out = capsys.readouterr().out
    assert "Task spec:" in out
    assert "failure spec" in out
    assert "halt spec" not in out

    res = task_show_command(SimpleNamespace(task_id="t1", json=True))
    assert res == 0
    data = json.loads(capsys.readouterr().out)
    assert data["spec"] == "failure spec"


def test_task_show_spec_json_verbatim_untruncated(tmp_path, monkeypatch, capsys):
    """--json carries the spec verbatim, untruncated (Fixes #117)."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    mgr, session = _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)

    long_spec = "word " * 500  # well past the 400-char human truncation limit
    mgr.update_decision(session.session_id, "halt", {
        "t1": {
            "final_decision": "halt",
            "halt_type": "blocked",
            "phase": "pre_execute",
            "task_spec": long_spec,
            "validator_results": [],
        }
    })

    res = task_show_command(SimpleNamespace(task_id="t1", json=True))
    assert res == 0
    data = json.loads(capsys.readouterr().out)
    assert data["spec"] == long_spec

    # Human form is truncated with a pointer to --json.
    res = task_show_command(SimpleNamespace(task_id="t1", json=False))
    assert res == 0
    out = capsys.readouterr().out
    assert "…" in out
    assert "truncated — full spec: snodo task show t1 --json" in out


def test_task_show_no_spec_still_renders(tmp_path, monkeypatch, capsys):
    """A record with no spec still renders everything else without error (Fixes #117)."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    mgr, session = _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)

    mgr.update_decision(session.session_id, "halt", {
        "t1": {
            "final_decision": "halt",
            "halt_type": "blocked",
            "phase": "pre_execute",
            "reason": "no spec here",
            "validator_results": [{"validator_id": "security", "severity": "blocker", "justification": "x"}],
        }
    })

    res = task_show_command(SimpleNamespace(task_id="t1", json=False))
    assert res == 0
    out = capsys.readouterr().out
    assert "Task:    t1" in out
    assert "reason:         no spec here" in out
    assert "Task spec:" not in out

    res = task_show_command(SimpleNamespace(task_id="t1", json=True))
    assert res == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["spec"] is None
    assert data["halt"]["reason"] == "no spec here"


# ============================================================================
# 6. task_abandon_command tests
# ============================================================================

def test_task_abandon_missing_args(tmp_path, monkeypatch, capsys):
    """task_abandon_command validates task_id and project root."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))

    res = task_abandon_command(SimpleNamespace(task_id=""))
    assert res == 1
    assert "Usage: snodo task abandon" in capsys.readouterr().err

    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: None)
    res = task_abandon_command(SimpleNamespace(task_id="t1"))
    assert res == 1
    assert "Not inside a snodo project." in capsys.readouterr().err


def test_task_abandon_deletes_session_context_and_branch(tmp_path, monkeypatch, capsys):
    """task_abandon_command clears failure context and deletes git branch."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    mgr, session = _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)
    mgr.update_decision(session.session_id, "task_failure", {"t1": {"branch": "task/t1"}})

    mock_git = MagicMock()
    mock_head = MagicMock()
    mock_head.name = "task/t1"
    mock_git.repo.heads = [mock_head]
    monkeypatch.setattr("snodo.tools.git.GitMCP", lambda p: mock_git)
    monkeypatch.setattr("snodo.infrastructure.worktree.remove_worktree", lambda p, t: None)

    # Trigger update_decision exception branch
    monkeypatch.setattr(mgr, "update_decision", MagicMock(side_effect=Exception("db write error")))

    res = task_abandon_command(SimpleNamespace(task_id="t1"))
    assert res == 0
    assert "Task abandoned." in capsys.readouterr().out
    mock_git.repo.git.branch.assert_called_with("-D", "task/t1")


def test_task_abandon_git_exception(tmp_path, monkeypatch, capsys):
    """task_abandon_command handles git errors gracefully."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)

    def bad_git(p):
        raise RuntimeError("git failed")

    monkeypatch.setattr("snodo.tools.git.GitMCP", bad_git)

    res = task_abandon_command(SimpleNamespace(task_id="t1"))
    assert res == 1
    assert "Error deleting branch: git failed" in capsys.readouterr().err


# ============================================================================
# 7. task_prune_command tests
# ============================================================================

def test_task_prune_outside_project_or_no_failures(tmp_path, monkeypatch, capsys):
    """task_prune_command returns error outside project or when no task failures exist."""
    # Outside project
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: None)
    assert task_prune_command(SimpleNamespace(stale_days=7)) == 1

    # No task failures
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)
    res = task_prune_command(SimpleNamespace(stale_days=7))
    assert res == 0
    assert "No task branches to prune." in capsys.readouterr().out


def test_task_prune_no_stale_branches(tmp_path, monkeypatch, capsys):
    """task_prune_command returns 0 when no branches exceed stale_days."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    mgr, session = _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)

    mgr.update_decision(session.session_id, "task_failure", {
        "t1": {"branch": "task/t1", "timestamp": datetime.now(timezone.utc).isoformat()}
    })

    res = task_prune_command(SimpleNamespace(stale_days=7))
    assert res == 0
    assert "No task branches older than 7 days." in capsys.readouterr().out


def test_task_prune_user_confirms_and_deletes(tmp_path, monkeypatch, capsys):
    """task_prune_command deletes stale branches when user confirms 'y'."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    mgr, session = _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)

    stale_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    mgr.update_decision(session.session_id, "task_failure", {
        "t1": {"branch": "task/t1", "timestamp": stale_ts},
        "t_invalid": {"branch": "task/t_invalid", "timestamp": "not_a_valid_date"},
    })

    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    mock_git = MagicMock()
    mock_head = MagicMock()
    mock_head.name = "task/t1"
    mock_git.repo.heads = [mock_head]
    monkeypatch.setattr("snodo.tools.git.GitMCP", lambda p: mock_git)
    monkeypatch.setattr("snodo.infrastructure.worktree.remove_worktree", lambda p, t: None)

    res = task_prune_command(SimpleNamespace(stale_days=7))
    assert res == 0
    out = capsys.readouterr().out
    assert "Found 1 stale task branch" in out
    assert "Deleted 1 stale branch(es)." in out


def test_task_prune_user_aborts(tmp_path, monkeypatch, capsys):
    """task_prune_command handles 'n' user input and EOF interrupts."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    mgr, session = _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)

    stale_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    mgr.update_decision(session.session_id, "task_failure", {
        "t1": {"branch": "task/t1", "timestamp": stale_ts}
    })

    # User declines
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    res = task_prune_command(SimpleNamespace(stale_days=7))
    assert res == 0
    assert "Aborted." in capsys.readouterr().out

    # EOFError interrupt
    def raise_eof(prompt=""):
        raise EOFError()

    monkeypatch.setattr("builtins.input", raise_eof)
    res = task_prune_command(SimpleNamespace(stale_days=7))
    assert res == 1
    assert "Aborted." in capsys.readouterr().out


def test_task_prune_exception_handling(tmp_path, monkeypatch, capsys):
    """task_prune_command handles errors during prune operation."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    mgr, session = _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)

    stale_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    mgr.update_decision(session.session_id, "task_failure", {
        "t1": {"branch": "task/t1", "timestamp": stale_ts}
    })

    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr("snodo.tools.git.GitMCP", lambda p: 1 / 0)

    res = task_prune_command(SimpleNamespace(stale_days=7))
    assert res == 1
    assert "Error pruning branches:" in capsys.readouterr().err


# ============================================================================
# 8. Typer CLI entrypoint wrappers & callback
# ============================================================================

def test_task_callback_prints_help(capsys):
    """_task_callback prints help when no subcommand invoked."""
    ctx = MagicMock()
    ctx.invoked_subcommand = None
    ctx.get_help.return_value = "Task help text"
    _task_callback(ctx)
    assert "Task help text" in capsys.readouterr().out


def test_typer_app_command_wrappers(tmp_path, monkeypatch):
    """Typer app command wrappers delegate correctly."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: None)

    assert task_list() == 1
    assert task_show("t1", json=True) == 1
    assert task_abandon("t1") == 1
    assert task_prune(7) == 1
    assert task_review("t1", verdict="accepted", report=False, days=30, json=True) == 1
    assert task_report(days=30, json=True) == 1


def test_task_list_git_exception_does_not_convert_completed_to_merged(tmp_path, monkeypatch, capsys):
    """Git inspection exceptions leave completed tasks as 'completed', not guessed 'merged'."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    mgr, session = _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)

    mgr.update_decision(session.session_id, "halt", {
        "t1": {
            "status": "completed",
            "halt_type": "completed",
            "final_decision": "completed",
            "task_id": "t1",
        }
    })

    # Force GitMCP to fail
    monkeypatch.setattr("snodo.tools.git.GitMCP", MagicMock(side_effect=RuntimeError("git error")))

    res = task_list_command(SimpleNamespace())
    assert res == 0
    out = capsys.readouterr().out
    assert "t1" in out
    assert "completed" in out
    assert "merged" not in out


def test_task_prune_untimestamped_halt_record_pruned(tmp_path, monkeypatch, capsys):
    """Untimestamped halt records fall back to session mtime/epoch and are pruned when stale."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    mgr, session = _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)

    # Record halt with NO timestamp
    mgr.update_decision(session.session_id, "halt", {
        "t_old": {
            "status": "completed",
            "halt_type": "completed",
            "task_id": "t_old",
        }
    })

    # Set session last_updated to an old date (> 7 days)
    session.last_updated = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    monkeypatch.setattr(mgr, "get_active_session", lambda m, p: session)

    mock_git = MagicMock()
    b_old = MagicMock()
    b_old.name = "task/t_old"
    b_old.commit.committed_date = 100000
    mock_git.repo.heads = [b_old]
    monkeypatch.setattr("snodo.tools.git.GitMCP", lambda p: mock_git)
    monkeypatch.setattr("snodo.infrastructure.worktree.remove_worktree", lambda p, t: None)

    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    res = task_prune_command(SimpleNamespace(stale_days=7))
    assert res == 0
    out = capsys.readouterr().out
    assert "Found 1 stale task branch" in out
    assert "t_old" in out


def test_dispatched_task_status_is_in_progress(tmp_path, monkeypatch):
    """A task known only from classification (dispatched, no halt record) reports in_progress."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    mgr, session = _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)

    mgr.update_decision(session.session_id, "classification", {
        "t_dispatched": {
            "flow_type": "feature",
            "task_spec": "some work",
        }
    })

    tasks = _get_all_task_branches(str(tmp_path))
    assert "t_dispatched" in tasks
    assert tasks["t_dispatched"]["status"] == "in_progress"


def test_untimestamped_task_fallback_timestamp_not_unconditionally_stale(tmp_path, monkeypatch, capsys):
    """An untimestamped task record without session timestamp falls back to now() and is not unconditionally stale."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    mgr, session = _setup_project_with_session(tmp_path, mode="dev", monkeypatch=monkeypatch)

    mgr.update_decision(session.session_id, "classification", {
        "t_recent": {
            "flow_type": "feature",
            "task_spec": "recent work",
        }
    })

    # Clear any session timestamps to test fallback
    session.updated_at = ""
    session.created_at = ""
    monkeypatch.setattr(mgr, "get_active_session", lambda m, p: session)

    task_prune_command(SimpleNamespace(stale_days=7))
    out = capsys.readouterr().out
    assert "No task branches older than 7 days." in out or "No task branches to prune." in out

