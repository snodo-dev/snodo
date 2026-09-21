"""Tests for the machine-readable accumulated run-record report."""

import json

from snodo.cli.commands.runs_cmd import runs_command
from types import SimpleNamespace


def _write_task(root, task_id, state):
    task_dir = root / ".snodo" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "state.json").write_text(json.dumps(state))


def test_runs_json_has_schema_one_row_per_completed_run_and_absent_measurements(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".snodo").mkdir()
    _write_task(tmp_path, "task_measured", {
        "task_id": "task_measured",
        "status": "completed",
        "completed_at": 20.0,
        "cost": {
            "tokens": {"total_tokens": 0},
            "turns": 0,
            "attempts": None,
            "duration_seconds": 0.0,
            "change_size": None,
            "provenance": {"model": "mock", "coder": None},
        },
    })
    _write_task(tmp_path, "task_running", {
        "task_id": "task_running",
        "status": "running",
        "cost": {"tokens": {"total_tokens": 99}},
    })

    assert runs_command(SimpleNamespace(json=True)) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["schema"] == "snodo.runs.v1"
    assert payload["ok"] is True
    assert len(payload["runs"]) == 1
    record = payload["runs"][0]
    assert record["task_id"] == "task_measured"
    assert record["cost"]["tokens"]["total_tokens"] == 0
    assert record["cost"]["turns"] == 0
    assert "attempts" not in record["cost"]
    assert "change_size" not in record["cost"]
    assert "coder" not in record["cost"]["provenance"]
