"""The liveness snapshot is validated against its declared wire shape."""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import TypeAdapter

from snodo.infrastructure import cloud_liveness


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_fixture_snapshot_validates_against_declared_type(tmp_path):
    root = tmp_path / "project"
    _write(root / ".snodo" / "plans" / "wave8" / "status.json", {
        "tasks": {"2.1": "in_progress", "2.2": "pending"},
    })
    _write(root / ".snodo" / "tasks" / "t_alpha" / "state.json", {
        "status": "running", "started_at": 1787000000.0,
    })
    home = tmp_path / "home"
    _write(home / "sessions" / "sess_test.json", {
        "project_id": "local:test123",
        "created_at": "2026-09-02T21:10:04+00:00",
    })

    with patch.object(cloud_liveness, "resolve_home", lambda: home):
        snapshot = cloud_liveness.build_liveness_snapshot(
            "sess_test", str(root),
        )

    assert snapshot is not None
    TypeAdapter(cloud_liveness.LivenessSnapshot).validate_python(snapshot)


@pytest.mark.parametrize("statuses", [
    {"1.1": "blocked", "1.2": "unmerged"},
    {"1.1": "completed", "1.2": "errored"},
])
def test_all_settled_but_not_completed_plan_keeps_every_task(tmp_path, statuses):
    root = tmp_path / "project"
    plan = root / ".snodo" / "plans" / "needs_person"
    _write(plan / "status.json", {"tasks": {
        task_id: {"status": status, "completed_at": "2026-09-23T12:00:00+00:00"}
        for task_id, status in statuses.items()
    }})
    (plan / "plan.yml").write_text("waves:\n  - id: 1\n    tasks: ['1.1', '1.2']\n")

    snapshot = cloud_liveness.build_liveness_snapshot("sess", str(root))

    assert snapshot is not None
    tasks = snapshot["plans"][0]["waves"][0]["tasks"]
    assert [(t["id"], t["status"]) for t in tasks] == list(statuses.items())
    assert all(t["completed_at"] == "2026-09-23T12:00:00+00:00" for t in tasks)
    TypeAdapter(cloud_liveness.LivenessSnapshot).validate_python(snapshot)


def test_pending_only_wave_and_completed_wave_in_live_plan(tmp_path):
    root = tmp_path / "project"
    plan = root / ".snodo" / "plans" / "in_play"
    _write(plan / "status.json", {"tasks": {
        "1.1": {"status": "completed", "completed_at": 1787000300.0},
        "2.1": "pending",
    }})
    (plan / "plan.yml").write_text(
        "waves:\n  - id: 1\n    tasks: ['1.1']\n"
        "  - id: 2\n    tasks: ['2.1', '2.2']\n"
    )
    _write(root / ".snodo" / "tasks" / "1.1" / "state.json", {
        "status": "completed", "started_at": 1787000000.0,
    })

    snapshot = cloud_liveness.build_liveness_snapshot("sess", str(root))

    assert snapshot is not None
    completed, pending = snapshot["plans"][0]["waves"]
    assert completed["tasks"][0]["started_at"] == datetime.fromtimestamp(
        1787000000.0, UTC,
    ).isoformat()
    assert completed["tasks"][0]["completed_at"] == datetime.fromtimestamp(
        1787000300.0, UTC,
    ).isoformat()
    assert pending["tasks"] == [
        {"id": "2.1", "status": "pending"},
        {"id": "2.2", "status": "pending"},
    ]
    TypeAdapter(cloud_liveness.LivenessSnapshot).validate_python(snapshot)


def test_fully_completed_plan_remains_counts_only(tmp_path):
    root = tmp_path / "project"
    plan = root / ".snodo" / "plans" / "finished"
    _write(plan / "status.json", {"tasks": {"1.1": "completed"}})
    (plan / "plan.yml").write_text("waves:\n  - id: 1\n    tasks: ['1.1']\n")

    snapshot = cloud_liveness.build_liveness_snapshot("sess", str(root))

    assert snapshot is not None
    assert snapshot["plans"] == [{
        "name": "finished", "total": 1, "status_counts": {"completed": 1},
    }]
    TypeAdapter(cloud_liveness.LivenessSnapshot).validate_python(snapshot)
