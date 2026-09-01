"""Tests for snodo task review and review report commands (Fixes #70, ADR 035)."""

import json
from types import SimpleNamespace

from snodo.infrastructure.audit import AuditLog

from snodo.cli.commands.task_cmd import (
    task_report_command,
    task_review_command,
    task_review_pending_command,
)


def test_task_review_invalid_verdict(tmp_path, monkeypatch):
    """Invalid verdict returns exit code 1."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))

    args = SimpleNamespace(
        task_id="task_123",
        verdict="invalid_verdict",
        notes="",
        report=False,
        days=30,
        json=False,
    )
    res = task_review_command(args)
    assert res == 1


def test_task_review_records_audit_event(tmp_path, monkeypatch):
    """task review records human_review_recorded in audit log."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    args = SimpleNamespace(
        task_id="task_123",
        verdict="accepted",
        notes="looks good",
        report=False,
        days=30,
        json=False,
    )
    res = task_review_command(args)
    assert res == 0

    events = audit_log.get_history("human_review_recorded")
    assert len(events) == 1
    ev = events[0]
    assert ev.data["task_ref"] == "task_123"
    assert ev.data["verdict"] == "accepted"
    assert ev.data["notes"] == "looks good"


def test_task_report_calculates_acceptance_rate(tmp_path, monkeypatch, capsys):
    """task report calculates percentage of completed tasks accepted unchanged."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    # Log completed tasks
    audit_log.append_event("task_complete", {"op": "task_complete", "task_ref": "t1"})
    audit_log.append_event("task_complete", {"op": "task_complete", "task_ref": "t2"})
    audit_log.append_event("task_complete", {"op": "task_complete", "task_ref": "t3"})

    # Log merged units and human reviews: t1 accepted, t2 amended, t3 discarded
    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t1", "merge_sha": "a" * 40})
    audit_log.append_event("human_review_recorded", {"op": "human_review_recorded", "task_ref": "t1", "merge_sha": "a" * 40, "verdict": "accepted"})
    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t2", "merge_sha": "b" * 40})
    audit_log.append_event("human_review_recorded", {"op": "human_review_recorded", "task_ref": "t2", "merge_sha": "b" * 40, "verdict": "amended"})
    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t3", "merge_sha": "c" * 40})
    audit_log.append_event("human_review_recorded", {"op": "human_review_recorded", "task_ref": "t3", "merge_sha": "c" * 40, "verdict": "discarded"})

    args = SimpleNamespace(days=30, json=False)
    res = task_report_command(args)
    assert res == 0

    out = capsys.readouterr().out
    assert "Completed tasks (task_complete): 3" in out
    assert "Merged units (task_merged):      3" in out
    assert "Reviewed tasks:            3" in out
    assert "Accepted unchanged:    1" in out
    assert "Amended by operator:   1" in out
    assert "Discarded / reverted:  1" in out
    assert "Unchanged Acceptance Rate: 33.3%" in out


def test_task_report_json(tmp_path, monkeypatch, capsys):
    """task report --json emits machine-readable JSON."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    audit_log.append_event("task_complete", {"op": "task_complete", "task_ref": "t1"})
    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t1", "merge_sha": "a" * 40})
    audit_log.append_event("human_review_recorded", {"op": "human_review_recorded", "task_ref": "t1", "merge_sha": "a" * 40, "verdict": "accepted"})

    args = SimpleNamespace(days=30, json=True)
    res = task_report_command(args)
    assert res == 0

    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["schema"] == "snodo.task_review_report.v1"
    assert data["completed_tasks"] == 1
    assert data["merged_units"] == 1
    assert data["accepted_unchanged"] == 1
    assert data["acceptance_rate_pct"] == 100.0


def test_report_counts_two_merges_of_same_branch(tmp_path, monkeypatch, capsys):
    """Two merges of the same branch with different verdicts are two rows
    (Fixes #101). Against current main this shows one task."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    audit_log.append_event("task_merged", {
        "op": "task_merged", "task_ref": "agent-a", "branch": "agent-a",
        "merge_sha": "a" * 40,
    })
    audit_log.append_event("human_review_recorded", {
        "op": "human_review_recorded", "task_ref": "agent-a", "branch": "agent-a",
        "merge_sha": "a" * 40, "verdict": "accepted",
    })
    audit_log.append_event("task_merged", {
        "op": "task_merged", "task_ref": "agent-a", "branch": "agent-a",
        "merge_sha": "b" * 40,
    })
    audit_log.append_event("human_review_recorded", {
        "op": "human_review_recorded", "task_ref": "agent-a", "branch": "agent-a",
        "merge_sha": "b" * 40, "verdict": "amended",
    })

    args = SimpleNamespace(days=30, json=True)
    res = task_report_command(args)
    assert res == 0

    data = json.loads(capsys.readouterr().out)
    assert data["merged_units"] == 2
    assert data["accepted_unchanged"] == 1
    assert data["amended"] == 1


def test_task_report_unreviewed_merge_never_counts_as_accepted(tmp_path, monkeypatch, capsys):
    """An unreviewed merge is counted as unreviewed, never as accepted — the
    report's rate is over reviewed tasks only (Fixes #83)."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    # t1 merged with a verdict recorded at merge time (accepted); t2 merged
    # with no verdict (recorded as unreviewed at merge time).
    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t1", "merge_sha": "a" * 40})
    audit_log.append_event("human_review_recorded", {"op": "human_review_recorded", "task_ref": "t1", "merge_sha": "a" * 40, "verdict": "accepted"})
    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t2", "merge_sha": "b" * 40})
    audit_log.append_event("human_review_recorded", {"op": "human_review_recorded", "task_ref": "t2", "merge_sha": "b" * 40, "verdict": "unreviewed"})

    args = SimpleNamespace(days=30, json=True)
    res = task_report_command(args)
    assert res == 0

    data = json.loads(capsys.readouterr().out)
    assert data["merged_units"] == 2
    assert data["total_reviewed"] == 1
    assert data["accepted_unchanged"] == 1
    assert data["unreviewed"] == 1
    # The rate is over reviewed tasks only — the unreviewed merge does not
    # dilute it, and it does not inflate it either.
    assert data["acceptance_rate_pct"] == 100.0


def test_task_report_no_reviews_human_shows_na_not_zero(tmp_path, monkeypatch, capsys):
    """A window with merged units but no reviews reports no acceptance rate
    (n/a), not a misleading 0.0% — zero reviews is not zero acceptance."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    # Completed and merged, but nothing reviewed.
    audit_log.append_event("task_complete", {"op": "task_complete", "task_ref": "t1"})
    audit_log.append_event("task_complete", {"op": "task_complete", "task_ref": "t2"})
    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t1", "merge_sha": "a" * 40})
    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t2", "merge_sha": "b" * 40})

    args = SimpleNamespace(days=30, json=False)
    res = task_report_command(args)
    assert res == 0

    out = capsys.readouterr().out
    assert "Unchanged Acceptance Rate: n/a" in out
    assert "Unchanged Acceptance Rate: 0.0%" not in out
    # The underlying counts are still reported; only the rate is suppressed.
    assert "Completed tasks (task_complete): 2" in out
    assert "Merged units (task_merged):      2" in out


def test_task_report_no_reviews_json_rate_is_null(tmp_path, monkeypatch, capsys):
    """The --json output agrees with the human one: no reviews means the
    acceptance rate is null, not 0.0."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t1", "merge_sha": "a" * 40})
    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t2", "merge_sha": "b" * 40})

    args = SimpleNamespace(days=30, json=True)
    res = task_report_command(args)
    assert res == 0

    data = json.loads(capsys.readouterr().out)
    assert data["total_reviewed"] == 0
    assert data["acceptance_rate_pct"] is None


def test_task_report_empty_window_no_percentages(tmp_path, monkeypatch, capsys):
    """A window with nothing merged has no denominator for either rate: both
    the reviewed percentage and the acceptance rate show n/a, never 0.0%."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    args = SimpleNamespace(days=30, json=False)
    res = task_report_command(args)
    assert res == 0

    out = capsys.readouterr().out
    assert "Reviewed tasks:            0 (n/a)" in out
    assert "Unchanged Acceptance Rate: n/a" in out
    assert "0.0%" not in out


# ============================================================================
# task review --pending tests (Fixes #120)
# ============================================================================

def _pending_args(json=False):
    return SimpleNamespace(task_id="", verdict=None, notes=None, report=False, pending=True, days=30, json=json)


def test_pending_excludes_reviewed_and_includes_unreviewed(tmp_path, monkeypatch, capsys):
    """--pending lists unreviewed merged units and excludes reviewed ones."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t1", "branch": "task/t1", "merge_sha": "a" * 40})
    audit_log.append_event("human_review_recorded", {"op": "human_review_recorded", "task_ref": "t1", "merge_sha": "a" * 40, "verdict": "accepted"})
    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t2", "branch": "task/t2", "merge_sha": "b" * 40})

    res = task_review_pending_command(_pending_args())
    assert res == 0

    out = capsys.readouterr().out
    assert "1 merged unit(s) awaiting review" in out
    assert "b" * 40 in out  # unreviewed t2
    assert "a" * 40 not in out  # reviewed t1 excluded


def test_pending_orders_newest_first(tmp_path, monkeypatch, capsys):
    """--pending orders merged units newest first by merge timestamp."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t_old", "merge_sha": "old" * 20, "timestamp": "2026-01-01T00:00:00+00:00"})
    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t_new", "merge_sha": "new" * 20, "timestamp": "2026-02-01T00:00:00+00:00"})

    res = task_review_pending_command(_pending_args(json=True))
    assert res == 0

    data = json.loads(capsys.readouterr().out)
    assert data["count"] == 2
    assert data["pending"][0]["unit_id"] == "new" * 20
    assert data["pending"][1]["unit_id"] == "old" * 20


def test_pending_json_shape_stable(tmp_path, monkeypatch, capsys):
    """--pending --json emits a stable machine-readable shape."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t1", "branch": "task/t1", "merge_sha": "a" * 40, "timestamp": "2026-01-01T00:00:00+00:00"})

    res = task_review_pending_command(_pending_args(json=True))
    assert res == 0

    data = json.loads(capsys.readouterr().out)
    assert data["schema"] == "snodo.task_review_pending.v1"
    assert data["ok"] is True
    assert data["count"] == 1
    row = data["pending"][0]
    assert set(row.keys()) == {"unit_id", "task_id", "branch", "merge_timestamp", "spec_excerpt"}
    assert row["unit_id"] == "a" * 40
    assert row["task_id"] == "t1"
    assert row["branch"] == "task/t1"
    assert row["merge_timestamp"] == "2026-01-01T00:00:00+00:00"
    assert row["spec_excerpt"] == ""


def test_pending_empty_case(tmp_path, monkeypatch, capsys):
    """--pending with no merged units prints a clear message and exits 0."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    res = task_review_pending_command(_pending_args())
    assert res == 0
    assert "No merged units awaiting review." in capsys.readouterr().out

    res = task_review_pending_command(_pending_args(json=True))
    assert res == 0
    data = json.loads(capsys.readouterr().out)
    assert data["count"] == 0
    assert data["pending"] == []


def test_pending_is_read_only(tmp_path, monkeypatch, capsys):
    """--pending never creates, mutates or clears a review record."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t1", "merge_sha": "a" * 40})

    before = len(audit_log.events)
    res = task_review_pending_command(_pending_args())
    assert res == 0
    after = len(audit_log.events)
    assert after == before  # no new audit events written
    assert len(audit_log.get_history("human_review_recorded")) == 0


def test_pending_delegates_from_review_command(tmp_path, monkeypatch, capsys):
    """task review --pending delegates to the pending list."""
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t1", "merge_sha": "a" * 40})

    args = SimpleNamespace(task_id="", verdict=None, notes=None, report=False, pending=True, days=30, json=True)
    res = task_review_command(args)
    assert res == 0
    data = json.loads(capsys.readouterr().out)
    assert data["schema"] == "snodo.task_review_pending.v1"
    assert data["count"] == 1


def test_pending_spec_excerpt_from_session_halt(tmp_path, monkeypatch, capsys):
    """--pending pulls the one-line spec excerpt from the session halt payload."""
    from pathlib import Path

    from snodo.infrastructure.session import SessionManager
    from snodo.infrastructure.state import ProjectState, write_state

    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    audit_log = AuditLog(str(tmp_path / "audit.log"))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda project_id=None: audit_log)

    sessions_dir = Path(tmp_path) / ".snodo" / "sessions"
    mgr = SessionManager(audit_log=audit_log, sessions_dir=sessions_dir)
    session = mgr.create_session("dev", str(tmp_path))
    write_state(str(tmp_path), ProjectState(current_mode="dev", active_session={"dev": session.session_id}))
    monkeypatch.setattr("snodo.infrastructure.session.SessionManager", lambda *a, **kw: mgr)

    mgr.update_decision(session.session_id, "halt", {
        "t1": {"task_spec": "Implement a login endpoint with tests and documentation"},
    })

    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t1", "merge_sha": "a" * 40})

    res = task_review_pending_command(_pending_args(json=True))
    assert res == 0
    data = json.loads(capsys.readouterr().out)
    assert data["pending"][0]["spec_excerpt"] == "Implement a login endpoint with tests and documentation"
