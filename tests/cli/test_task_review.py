"""Tests for snodo task review and review report commands (Fixes #70, ADR 035)."""

import json
from types import SimpleNamespace
from snodo.cli.commands.task_cmd import task_review_command, task_report_command
from snodo.infrastructure.audit import AuditLog


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
    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t1"})
    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t2"})
    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t3"})

    # Log human reviews: t1 accepted, t2 amended, t3 discarded
    audit_log.append_event("human_review_recorded", {"op": "human_review_recorded", "task_ref": "t1", "verdict": "accepted"})
    audit_log.append_event("human_review_recorded", {"op": "human_review_recorded", "task_ref": "t2", "verdict": "amended"})
    audit_log.append_event("human_review_recorded", {"op": "human_review_recorded", "task_ref": "t3", "verdict": "discarded"})

    args = SimpleNamespace(days=30, json=False)
    res = task_report_command(args)
    assert res == 0

    out = capsys.readouterr().out
    assert "Completed tasks:           3" in out
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

    audit_log.append_event("task_merged", {"op": "task_merged", "task_ref": "t1"})
    audit_log.append_event("human_review_recorded", {"op": "human_review_recorded", "task_ref": "t1", "verdict": "accepted"})

    args = SimpleNamespace(days=30, json=True)
    res = task_report_command(args)
    assert res == 0

    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["schema"] == "snodo.task_review_report.v1"
    assert data["total_completed"] == 1
    assert data["accepted_unchanged"] == 1
    assert data["acceptance_rate_pct"] == 100.0
