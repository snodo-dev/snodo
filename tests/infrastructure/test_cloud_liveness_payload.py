"""The liveness snapshot is validated against its declared wire shape."""

import json
from pathlib import Path
from unittest.mock import patch

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
