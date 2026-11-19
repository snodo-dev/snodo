import json
from datetime import datetime, timezone

from snodo.web.gates import gate_records, summarize_gates


NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _project(tmp_path, status="completed", *, validator="lint", timestamp=NOW.timestamp(), retry=True):
    root = tmp_path
    snodo = root / ".snodo"
    _write(snodo / "plans" / "plan-a" / "status.json", {"tasks": {"task-a": status}})
    task_dir = snodo / "tasks" / "task-a"
    _write(task_dir / "attempts" / "001.json", {
        "task_id": "task-a", "plan_name": "plan-a", "status": "blocked",
        "completed_at": timestamp,
        "halt": {"status": "blocked", "validator_results": ([
            {"validator_id": validator, "severity": "blocker"}
        ] if validator else [])},
    })
    if retry:
        _write(snodo / "tasks" / "task-a_fix_1" / "state.json", {"task_id": "task-a_fix_1"})
    return root


def test_retry_completed_is_landed(tmp_path):
    records = gate_records(_project(tmp_path), 7, NOW)
    assert records == [{
        "task": "task-a", "plan": "plan-a", "validator": "lint",
        "outcome": "landed_after_retry", "when": NOW.isoformat(),
    }]


def test_retry_still_blocked_is_not_landed(tmp_path):
    records = gate_records(_project(tmp_path, status="blocked"), 7, NOW)
    assert records[0]["outcome"] == "not_landed"


def test_task_without_fix_record_is_counted(tmp_path):
    records = gate_records(_project(tmp_path, retry=False), 7, NOW)
    assert records[0]["validator"] == "lint"
    assert records[0]["outcome"] == "landed_after_retry"


def test_old_send_back_is_outside_window(tmp_path):
    old = NOW.timestamp() - 8 * 86400
    assert gate_records(_project(tmp_path, timestamp=old), 7, NOW) == []


def test_summary_groups_validator_and_unknown(tmp_path):
    records = gate_records(_project(tmp_path, validator=None), 7, NOW)
    records += [{"validator": "lint", "outcome": "landed_after_retry"}]
    assert summarize_gates(records) == {
        "sent_back": 2,
        "landed_after_retry": 2,
        "by_validator": [
            {"validator": "lint", "count": 1},
            {"validator": "unknown", "count": 1},
        ],
    }


def test_empty_project_records_and_summary(tmp_path):
    assert gate_records(tmp_path, 7, NOW) == []
    assert summarize_gates([]) == {"sent_back": 0, "landed_after_retry": 0, "by_validator": []}
