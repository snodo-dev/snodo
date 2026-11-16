"""Job payload coverage for ``snodo logs --json``."""

import json
from types import SimpleNamespace
from unittest.mock import patch


def _invoke(tmp_path, job_id, state, capsys, *, task=None):
    from snodo.cli.commands.logs_cmd import logs_command

    job_dir = tmp_path / ".snodo" / "jobs" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "state.json").write_text(json.dumps(state))
    if task is not None:
        (job_dir / "task.json").write_text(json.dumps(task))
    status = {
        "status": state.get("status", "completed"),
        "elapsed_seconds": state.get("elapsed_seconds"),
    }
    with patch("snodo.infrastructure.paths.require_project_root", return_value=str(tmp_path)), patch(
        "snodo.jobs.JobManager.get_status", return_value=status
    ), patch("snodo.jobs.JobManager._job_dir", return_value=job_dir):
        result = logs_command(SimpleNamespace(composite_id=job_id, watch=False, json=True))
    return result, json.loads(capsys.readouterr().out)


def test_job_json_groups_validator_and_coder_usage(tmp_path, capsys):
    records = [
        {"model": "m1", "served_model": "m1-served", "prompt_tokens": 100,
         "completion_tokens": 20, "duration_ms": 2000, "role": "validator:architecture",
         "task_id": "task_a"},
        {"model": "m2", "served_model": "m2-served", "input_tokens": 300,
         "output_tokens": 30, "duration_ms": 3000, "role": "coder", "task_id": "task_a"},
    ]
    result, data = _invoke(tmp_path, "j_usage", {
        "status": "completed", "elapsed_seconds": 10, "usage": records,
    }, capsys)

    assert result == 0
    assert data["schema"] == "snodo.logs.v1"
    assert data["ok"] is True
    assert data["kind"] == "job"
    assert data["id"] == "j_usage"
    assert data["usage"] == records
    assert data["totals"] == {
        "input_tokens": 400, "output_tokens": 50,
        "model_call_time_seconds": 5.0, "calls": 2,
    }
    assert {r["role"]: r for r in data["by_role"]}["validator:architecture"]["calls"] == 1
    assert sum(r["input_tokens"] for r in data["by_role"]) == data["totals"]["input_tokens"]
    assert sum(r["output_tokens"] for r in data["by_role"]) == data["totals"]["output_tokens"]
    assert sum(r["model_time_seconds"] for r in data["by_role"]) == data["totals"]["model_call_time_seconds"]
    assert sum(r["calls"] for r in data["by_role"]) == data["totals"]["calls"]


def test_plan_job_json_reads_its_usage(tmp_path, capsys):
    result, data = _invoke(tmp_path, "j_plan", {
        "status": "completed", "job_type": "plan", "usage": [
            {"role": "coder", "input_tokens": 7, "output_tokens": 2, "duration_ms": 1000},
        ],
    }, capsys, task={"plan_name": "plan-a"})
    assert result == 0
    assert data["usage"][0]["role"] == "coder"
    assert data["totals"]["input_tokens"] == 7


def test_job_json_without_usage_has_empty_rows_and_zero_totals(tmp_path, capsys):
    result, data = _invoke(tmp_path, "j_empty", {"status": "completed"}, capsys)
    assert result == 0
    assert data["usage"] == []
    assert data["by_role"] == []
    assert data["totals"] == {
        "input_tokens": 0, "output_tokens": 0,
        "model_call_time_seconds": 0.0, "calls": 0,
    }


def test_unknown_job_json_emits_uniform_error_on_stdout(tmp_path, capsys):
    from snodo.cli.commands.logs_cmd import logs_command

    (tmp_path / ".snodo").mkdir()
    with patch("snodo.infrastructure.paths.require_project_root", return_value=str(tmp_path)):
        result = logs_command(SimpleNamespace(composite_id="j_missing", watch=False, json=True))
    captured = capsys.readouterr()
    assert result != 0
    assert captured.err == ""
    data = json.loads(captured.out)
    assert data["schema"] == "snodo.logs.v1"
    assert data["ok"] is False
    assert "error" in data
