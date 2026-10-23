import json
from pathlib import Path

from snodo.task_runs import render_task_runs, task_runs, task_runs_data


def write(root: Path, relative: str, value: dict) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_job_ids_fallback_fixes_order_and_stable_data(tmp_path: Path, capsys) -> None:
    # Recorded-only job, fallback-only job, and a duplicate seen through both.
    write(tmp_path, ".snodo/tasks/t/state.json", {"task_id": "t", "job_id": "j2", "started_at": 20, "completed_at": 21, "status": "done"})
    write(tmp_path, ".snodo/tasks/t/attempts/1.json", {"job_id": "j1", "started_at": 10, "completed_at": 11, "status": "failed"})
    write(tmp_path, ".snodo/jobs/j1/task.json", {"task_id": "t", "plan_name": "p", "task_wave": 1})
    write(tmp_path, ".snodo/jobs/j1/state.json", {"status": "failed", "started_at": 10, "completed_at": 11})
    write(tmp_path, ".snodo/jobs/j2/task.json", {"task_id": "other"})
    write(tmp_path, ".snodo/jobs/j2/state.json", {"status": "done", "started_at": 20, "completed_at": 21})
    write(tmp_path, ".snodo/jobs/j3/task.json", {"retry_task_id": "t"})
    write(tmp_path, ".snodo/jobs/j3/state.json", {"status": "running", "started_at": 30})
    write(tmp_path, ".snodo/jobs/j4/task.json", {})
    write(tmp_path, ".snodo/jobs/j4/state.json", {"status": "done", "started_at": 40, "halt": {"task_id": "t_fix_1"}})
    write(tmp_path, ".snodo/tasks/t_fix_1/state.json", {"status": "done"})

    runs = task_runs(tmp_path, "t")
    assert [(r["job_id"], r["task_id"]) for r in runs] == [
        ("j1", "t"), ("j2", "t"), ("j3", "t"), ("j4", "t_fix_1")
    ]
    assert task_runs_data(runs)[0].keys() == {
        "job_id", "plan_name", "task_wave", "task_id", "started_at", "ended_at", "outcome"
    }
    assert runs[0]["plan_name"] == "p"
    assert task_runs(tmp_path, "missing") == []
    render_task_runs([])
    assert capsys.readouterr().out == ""
