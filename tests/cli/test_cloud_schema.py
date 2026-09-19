"""Tests for the published engine-to-cloud interface."""

import json

from jsonschema import validate
from typer.testing import CliRunner

from snodo.cli.commands.cloud_cmd import app


def test_cloud_schema_validates_real_snapshot_and_event_batch():
    result = CliRunner().invoke(app, ["schema", "--json"])

    assert result.exit_code == 0
    publication = json.loads(result.stdout)
    assert publication["interface_version"] == 2

    payloads = publication["payloads"]
    batch = {
        "session_id": "sess_schema",
        "project_path": "/work/project",
        "display_name": "project",
        "events": [{
            "sequence": 1,
            "timestamp": "2026-09-17T12:00:00+00:00",
            "event_type": "transition",
            "project_id": "local:project",
            "scope": "local",
            "data": {"status": "running"},
            "previous_hash": "0" * 64,
            "event_hash": "a" * 64,
        }],
    }
    snapshot = {
        "session_id": "sess_schema",
        "project_id": "local:project",
        "scope": "local",
        "display_name": "project",
        "run_started_at": None,
        "plans": [],
        "tasks": [],
        "jobs": [],
        "task_status_counts": {"running": 1},
        "job_status_counts": {},
        "last_event": {"event_type": "transition", "timestamp": "2026-09-17T12:00:00+00:00"},
        "last_activity_at": None,
        "snapshot_at": "2026-09-17T12:00:01+00:00",
    }

    validate(batch, payloads["cloud_ingest"])
    validate(snapshot, payloads["cloud_liveness"])
