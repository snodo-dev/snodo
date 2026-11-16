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
    assert missing["efficiency"] is None
    assert missing["efficiency_tasks"] == 0
    assert missing["unclassified"] == 0
    assert all(value["landed"] == 0 and value["share"] is None and value["median_flow_seconds"] is None and value["p85_flow_seconds"] is None for value in missing["by_type"].values())
    assert all(all(week[key] == 0 for key in ("feature", "defect", "debt", "risk", "unclassified")) for week in missing["by_week"])


def test_flow_metrics_groups_types_durations_and_weeks(tmp_path, monkeypatch):
    from snodo.web import flow

    now = datetime(2025, 1, 11, 12, tzinfo=timezone.utc)
    task_ids = {"feature-a": "feature", "feature-b": "feature", "debt-a": "debt", "defect-a": "defect", "risk-a": "risk", "unknown": None}
    setup_plan(tmp_path, {task: "completed" for task in task_ids})
    for index, task_id in enumerate(task_ids):
        completed = datetime(2025, 1, 6 + index, 12, tzinfo=timezone.utc)
        write_json(tmp_path / ".snodo" / "tasks" / task_id / "state.json", {
            "started_at": (completed.timestamp() - (index + 1) * 100),
            "completed_at": completed.isoformat(),
        })
    monkeypatch.setattr(flow, "task_flow_types", lambda _root: {
        task: kind for task, kind in task_ids.items() if kind is not None
    })

    result = flow.flow_metrics(tmp_path, 7, now)

    assert result["by_type"]["feature"]["landed"] == 2
    assert result["by_type"]["debt"]["landed"] == 1
    assert result["by_type"]["defect"]["landed"] == 1
    assert result["by_type"]["risk"]["landed"] == 1
    assert sum(result["by_type"][kind]["share"] for kind in ("feature", "defect", "debt", "risk")) == 1
    assert result["by_type"]["feature"]["share"] == 2 / 5
    assert result["by_type"]["feature"]["median_flow_seconds"] == 150
    assert result["by_type"]["feature"]["p85_flow_seconds"] == 185
    assert result["unclassified"] == 1
    assert sum(result["by_type"][kind]["landed"] for kind in ("feature", "defect", "debt", "risk")) == 5
    assert result["by_week"][0]["week_start"] == "2024-12-30"
    assert result["by_week"][1]["week_start"] == "2025-01-06"
    assert result["by_week"][1]["unclassified"] == 1


def test_efficiency_uses_gate_sendback_and_excludes_unknown_history(tmp_path):
    now = datetime(2025, 1, 3, 12, tzinfo=timezone.utc)
    setup_plan(tmp_path, {"clean": "completed", "retry": "completed", "unknown": "completed"})
    tasks = tmp_path / ".snodo" / "tasks"
    landed = now.isoformat()
    write_json(tasks / "clean" / "state.json", {"started_at": "2025-01-03T10:00:00Z", "completed_at": landed, "halt": {"attempts": {"total": 1}}})
    write_json(tasks / "retry" / "state.json", {"started_at": "2025-01-03T10:00:00Z", "completed_at": landed, "halt": {"status": "blocked", "attempts": {"total": 2}}})
    write_json(tasks / "retry" / "attempts" / "1.json", {"completed_at": "2025-01-03T11:30:00Z", "halt": {"status": "blocked"}})
    write_json(tasks / "unknown" / "state.json", {"started_at": "2025-01-03T10:00:00Z", "completed_at": landed})

    result = flow_metrics(tmp_path, 1, now)

    assert result["efficiency_tasks"] == 2
    assert result["efficiency"] == 0.875


def test_efficiency_uses_recovery_start_and_unknown_timestamp_is_excluded(tmp_path):
    now = datetime(2025, 1, 3, 12, tzinfo=timezone.utc)
    setup_plan(tmp_path, {"recovered": "completed", "missing": "completed"})
    tasks = tmp_path / ".snodo" / "tasks"
    write_json(tasks / "recovered" / "state.json", {"started_at": "2025-01-03T10:00:00Z", "completed_at": now.isoformat()})
    write_json(tasks / "recovered_fix_1" / "state.json", {"started_at": "2025-01-03T11:00:00Z"})
    write_json(tasks / "missing" / "state.json", {"started_at": "2025-01-03T10:00:00Z", "completed_at": now.isoformat()})
    write_json(tasks / "missing_fix_1" / "state.json", {})

    result = flow_metrics(tmp_path, 1, now)

    assert result["efficiency_tasks"] == 1
    assert result["efficiency"] == 0.5
