"""Pin the operator message shape for every classified task outcome."""

import json

import pytest

from snodo.jobs import notifications


@pytest.mark.parametrize(
    ("situation", "state", "events", "headline", "has_validator", "authorize"),
    [
        ("success", {"status": "completed", "exit_code": 0}, [], "Success", False, False),
        ("warn_recovered", {"status": "completed", "exit_code": 0, "halt": {"reason": "Recovered"}},
         [{"event_type": "subtask_spawned", "task_ref": "task-recovery"}], "Warn — recovered by task-recovery", False, False),
        ("human_decision", {"status": "failed", "exit_code": 1,
         "halt": {"type": "disagreement", "adjudicable": True, "reason": "Choose a direction"}},
         [{"event_type": "disagreement_escalated"}], "Waiting for a human", True, True),
        ("blocked", {"status": "failed", "exit_code": 1, "halt": {"reason": "Validation failed"}},
         [{"event_type": "recovery_exhausted"}], "Blocked", True, False),
        ("engine_fault", {"status": "failed", "exit_code": 1,
         "halt": {"type": "internal_error", "reason": "Worker crashed"}}, [], "Engine fault", False, False),
    ],
)
def test_notification_message_shape_per_situation(
    tmp_path, monkeypatch, situation, state, events, headline, has_validator, authorize
):
    job_id = f"job-{situation}"
    task_id = f"task-{situation}"
    job_dir = tmp_path / ".snodo" / "jobs" / job_id
    job_dir.mkdir(parents=True)
    halt = state.get("halt", {})
    validators = ([
        {"validator_id": "minor-check", "severity": "warn", "justification": "Less severe finding"},
        {"validator_id": "critical-check", "severity": "blocker",
         "justification": "Unsafe *input* <@operator>"},
    ] if has_validator else [])
    state = {**state, "started_at": 1}
    if halt or validators:
        state["halt"] = {**halt, "validator_results": validators}
    (job_dir / "task.json").write_text(json.dumps({"task_id": task_id}))
    (job_dir / "state.json").write_text(json.dumps(state))
    audit_path = tmp_path / ".snodo" / "audit.log"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"timestamp": "2026-01-01T00:00:00", "task_ref": task_id, **event}
        for event in events
    ]
    if has_validator or halt:
        records.append({"timestamp": "2026-01-01T00:00:00", "task_ref": task_id,
                        "event_type": "halt", "halt_type": halt.get("type", "blocked"),
                        "reason": halt.get("reason", "")})
    audit_path.write_text("".join(json.dumps(record) + "\n" for record in records))

    delivered = []
    monkeypatch.setattr(notifications, "_settings", lambda: {"events": ["job_finished"]})
    monkeypatch.setattr(notifications, "_targets", lambda _settings: [{"type": "webhook"}])
    monkeypatch.setattr(notifications, "send", lambda _target, payload: delivered.append(payload))

    notifications.monitor(str(tmp_path), job_id)

    payload = next(item for item in delivered if item["event"] == "job_finished")
    message = payload["message"]
    assert payload["situation"] == situation
    assert headline in message
    assert f"Inspect: snodo logs {job_id}" in message
    assert len(message) <= 1000
    command = f"snodo authorize {task_id}"
    if has_validator:
        assert "critical-check (blocker)" in message
        assert r"Unsafe \*input\* \<＠operator\>" in message
        assert "minor-check (warn)" in message
    if authorize:
        assert command in message
    else:
        assert command not in message
    if situation == "success":
        assert "critical-check" not in message
        assert "Authorize:" not in message
