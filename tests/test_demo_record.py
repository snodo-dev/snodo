"""Tests for demo-local run record extraction."""

import json

import pytest

from demo.record import build_record, find_new_task_id


def _state(tmp_path, task_id, state):
    path = tmp_path / ".snodo" / "tasks" / task_id / "state.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(state))
    return build_record(tmp_path, task_id, "provider/model", "governed")


def test_completed_task_is_passed(tmp_path):
    record = _state(
        tmp_path, "task-1", {"status": "completed", "cost": {"duration": 1.5}}
    )
    assert record["outcome"] == "passed"


def test_blocker_keeps_validator_justification(tmp_path):
    record = _state(
        tmp_path,
        "task-1",
        {
            "status": "blocked",
            "halt": {
                "halt_type": "blocker",
                "validator_results": [
                    {
                        "validator_id": "v1",
                        "severity": "blocker",
                        "justification": "must fix",
                    }
                ],
            },
        },
    )
    assert record["outcome"] == "blocked"
    assert record["validator_findings"][0]["justification"] == "must fix"


def test_escalate_is_halted(tmp_path):
    record = _state(
        tmp_path, "task-1", {"status": "blocked", "halt": {"halt_type": "escalate"}}
    )
    assert record["outcome"] == "halted"


def test_engine_fault_is_error_with_halt_type(tmp_path):
    record = _state(
        tmp_path,
        "task-1",
        {"status": "failed", "halt": {"halt_type": "internal_error"}},
    )
    assert (record["outcome"], record["halt_type"]) == ("error", "internal_error")


def test_missing_state_is_unknown(tmp_path):
    record = build_record(tmp_path, "missing", "provider/model", "bare")
    assert record["outcome"] == "unknown"


def test_absent_tokens_and_cost_stay_null(tmp_path):
    record = _state(tmp_path, "task-1", {"status": "completed"})
    assert record["input_tokens"] is None
    assert record["output_tokens"] is None
    assert record["cost_usd"] is None
    assert record["cost_source"] == "unavailable"


def test_new_task_id_found_from_before_and_after_listings():
    assert find_new_task_id(["old"], ["old", "new"]) == "new"
    with pytest.raises(ValueError, match="found 0"):
        find_new_task_id(["old"], ["old"])
    with pytest.raises(ValueError, match="found 2"):
        find_new_task_id([], ["one", "two"])
