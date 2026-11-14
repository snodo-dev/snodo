"""Regression coverage for halts retained after a later successful attempt."""

import json

from snodo.jobs import notifications
from snodo.jobs.outcome import classify_outcome


def test_stale_halt_does_not_override_successful_terminal_job():
    state = {"status": "completed", "exit_code": 0, "halt": {"reason": "Earlier attempt failed"}}
    assert classify_outcome(state, []).situation == "success"


def test_failed_job_with_halt_remains_blocked():
    state = {"status": "failed", "exit_code": 1, "halt": {"reason": "Attempt failed"}}
    assert classify_outcome(state, []).situation == "blocked"


def test_pending_escalation_remains_human_decision():
    state = {
        "status": "completed", "exit_code": 0,
        "halt": {"type": "disagreement", "adjudicable": True},
    }
    assert classify_outcome(state, [{"event_type": "disagreement_escalated"}]).situation == "human_decision"


def test_successful_recovery_remains_warn_recovered():
    state = {"status": "completed", "exit_code": 0, "halt": {"reason": "Earlier attempt failed"}}
    events = [{"event_type": "subtask_spawned", "task_ref": "task-recovery"}]
    result = classify_outcome(state, events)
    assert result.situation == "warn_recovered"
    assert result.recovery_task_id == "task-recovery"


def test_nonzero_engine_faults_remain_engine_faults():
    for halt_type in ("validator_error", "internal_error", "environment_error"):
        state = {"status": "failed", "exit_code": 1, "halt": {"type": halt_type}}
        assert classify_outcome(state, []).situation == "engine_fault"


def test_finished_job_notification_uses_terminal_outcome_with_stale_halt(tmp_path, monkeypatch):
    monkeypatch.setattr(notifications, "_settings", lambda: {"events": ["job_finished"]})
    for job_id, status, exit_code, expected in (
        ("j_success", "completed", 0, "Success"),
        ("j_failed", "failed", 1, "Blocked"),
    ):
        root = tmp_path / job_id
        job = root / ".snodo" / "jobs" / job_id
        job.mkdir(parents=True)
        (job / "task.json").write_text(json.dumps({"task_id": job_id}))
        (job / "state.json").write_text(json.dumps({
            "status": status,
            "exit_code": exit_code,
            "started_at": 1,
            "halt": {"reason": "Earlier attempt failed"},
        }))
        sent = []
        monkeypatch.setattr(notifications, "_notify", lambda *args: sent.append(args))

        notifications.monitor(str(root), job_id)

        finished = next(entry for entry in sent if entry[2] == "job_finished")
        message, details = finished[3], finished[4]
        assert expected in message
        assert details["situation"] == ("success" if expected == "Success" else "blocked")
        if expected == "Success":
            assert "Blocked" not in message
