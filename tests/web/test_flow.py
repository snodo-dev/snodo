import json
from datetime import datetime, timezone

from snodo.web.flow import flow_metrics


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def setup_plan(root, tasks):
    write_json(root / ".snodo" / "plans" / "plan" / "status.json", {"tasks": tasks})


def test_landing_window_daily_zeros_and_median(tmp_path):
    now = datetime(2025, 1, 3, 12, tzinfo=timezone.utc)
    setup_plan(tmp_path, {"a": "completed", "b": "completed", "old": "completed"})
    task_root = tmp_path / ".snodo" / "tasks"
    write_json(task_root / "a" / "state.json", {"started_at": 1735732800, "completed_at": "2025-01-02T12:00:00Z"})
    write_json(task_root / "b" / "state.json", {"started_at": 1735905500, "completed_at": "2025-01-03T12:00:00Z"})
    write_json(task_root / "old" / "state.json", {"started_at": 1735600000, "completed_at": "2024-12-30T12:00:00Z"})

    result = flow_metrics(tmp_path, 3, now)

    assert result["landed"] == 2
    assert [row["landed"] for row in result["landed_by_day"]] == [0, 1, 1]
    assert result["median_flow_seconds"] == 43250


def test_median_omits_missing_timing_and_first_try_unknown(tmp_path):
    now = datetime(2025, 1, 3, 12, tzinfo=timezone.utc)
    setup_plan(tmp_path, {"timed": "completed", "untimed": "completed"})
    write_json(tmp_path / ".snodo" / "tasks" / "timed" / "state.json", {
        "started_at": 1735905500, "completed_at": "2025-01-03T12:00:00Z",
    })
    write_json(tmp_path / ".snodo" / "tasks" / "untimed" / "state.json", {"completed_at": "2025-01-03T12:00:00Z"})

    result = flow_metrics(tmp_path, 3, now)

    assert result["median_flow_seconds"] == 100
    assert result["first_try_rate"] is None


def test_flow_load_and_missing_snodo(tmp_path):
    setup_plan(tmp_path, {"active": "in_progress", "done": "completed"})
    now = datetime(2025, 1, 3, 12, tzinfo=timezone.utc)

    assert flow_metrics(tmp_path, 3, now)["flow_load"] == 1
    missing = flow_metrics(tmp_path / "absent", 3, now)
    assert missing["landed"] == 0
    assert missing["median_flow_seconds"] is None
    assert missing["first_try_rate"] is None
