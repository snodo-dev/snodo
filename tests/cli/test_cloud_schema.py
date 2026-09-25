"""Tests for the published engine-to-cloud interface."""

import json

from jsonschema import validate
from typer.testing import CliRunner

from snodo.cli.commands.cloud_cmd import app


def test_cloud_schema_validates_real_snapshot_and_event_batch():
    result = CliRunner().invoke(app, ["schema", "--json"])

    assert result.exit_code == 0
    publication = json.loads(result.stdout)
    assert publication["interface_version"] == 6

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
    resumed_batch = {**batch, "events": [{**batch["events"][0],
        "event_type": "session_resumed", "data": {"previous_mode": "running"}}]}
    validate(resumed_batch, payloads["cloud_ingest"])
    historical_batch = {**batch, "events": [{**batch["events"][0],
        "event_type": "legacy_event_from_old_release", "data": {"old_field": "kept"}}]}
    validate(historical_batch, payloads["cloud_ingest"])
    validate(snapshot, payloads["cloud_liveness"])
    validate(batch, publication["payloads_by_version"]["5"]["cloud_ingest"])
    validate({
        "schema": "snodo.run-record",
        "version": 1,
        "record": {"task_id": "task_schema", "completed_at": 1.0},
    }, payloads["run_record"])


def test_cloud_schema_declares_every_event_data_key_from_contract():
    result = CliRunner().invoke(app, ["schema", "--json"])
    publication = json.loads(result.stdout)
    ingest = publication["payloads"]["cloud_ingest"]

    expected = {
        "project_announced": {"project_id", "scope", "display_name"},
        "readiness_checked": {"project_id", "scope", "display_name", "protocol_id", "score", "total_checks", "passed_checks", "repository_findings_count", "workstation_findings_count", "findings"},
        "dispatch": {"task_ref", "mode", "token_id", "artifacts_count"},
        "work_already_present": {"task_ref", "base_ref", "artifacts_count", "files"},
        "governance_check": {"task_ref", "mode", "constraints_checked"},
        "validate": {"phase", "task_ref", "validators_invoked", "results", "outcome", "policy_decision"},
        "task_classified": {"task_ref", "flow_type", "wave_id", "task_summary"},
        "wave_created": {"wave_id", "feature_description"},
        "task_complete": {"task_ref", "artifacts", "session_id", "commit", "change_size"},
        "task_merged": {"task_ref", "branch", "merge_sha", "spec", "session_id"},
        "halt": {"task_ref", "reason", "blocker_validators", "halt_type", "raw_halt_type"},
        "transition": {"from_mode", "to_mode", "task_ref"},
        "token_consumed": {"task_ref", "session_id"},
        "post_validation_route": {"decision", "task_ref"},
        "post_validate_bypassed": {"mode", "reason", "task_ref"},
        "session_started": {"session_id", "mode", "project_root"},
        "session_task_changed": {"old_task", "new_task"},
        "session_decision_updated": {"key", "value"},
        "recovery_resolved": {"depth", "attempts_used"},
        "recovery_internal_error": {"depth", "error"},
        "execution_failed": {"error", "task_ref"},
        "verification_executed": {"command", "commit", "returncode", "outcome", "validator_id", "working_directory", "output_tail"},
        "coder_test_run": {"command_type", "exit_code", "test_path", "turn_index", "job_id"},
        "test_modified": {"mutations", "task_id", "job_id"},
        "unverified_merge_blocked": {"task_ref", "branch", "target_commit", "reason", "session_id"},
    }
    item_schema = ingest["properties"]["events"]["items"]
    branches = item_schema["oneOf"] if "oneOf" in item_schema else item_schema["anyOf"]
    by_type = {
        ingest["$defs"][branch["$ref"].split("/")[-1]]["properties"]["event_type"]["const"]:
        ingest["$defs"][branch["$ref"].split("/")[-1]]
        for branch in branches
        if "const" in ingest["$defs"][branch["$ref"].split("/")[-1]]["properties"]["event_type"]
    }

    assert set(expected) <= set(by_type)
    for event_type, keys in expected.items():
        data = by_type[event_type]["properties"]["data"]
        data_schema = ingest["$defs"][data["$ref"].split("/")[-1]]
        assert keys <= set(data_schema["properties"])

    envelope = ingest["$defs"]
    event_branches = [
        value for name, value in envelope.items()
        if name.endswith("Event") and name != "OpaqueAuditEvent"
    ]
    assert len(event_branches) == len(by_type)
    assert all(branch["properties"]["timestamp"]["type"] == "string" for branch in event_branches)
    assert all(branch["properties"]["scope"]["enum"] == ["", "local", "remote"] for branch in event_branches)
    opaque = next(name for name in envelope if name.startswith("OpaqueAuditEvent"))
    assert "not" in envelope[opaque]["properties"]["event_type"]


def test_v6_schema_publishes_wave_one_shapes_and_accepts_v5_events():
    publication = json.loads(CliRunner().invoke(app, ["schema", "--json"]).stdout)
    v6 = publication["payloads"]
    ingest = v6["cloud_ingest"]
    item_schema = ingest["properties"]["events"]["items"]
    branches = item_schema.get("oneOf", item_schema.get("anyOf"))
    by_type = {
        ingest["$defs"][branch["$ref"].split("/")[-1]]["properties"]["event_type"]["const"]:
        ingest["$defs"][branch["$ref"].split("/")[-1]]
        for branch in branches
        if "const" in ingest["$defs"][branch["$ref"].split("/")[-1]]["properties"]["event_type"]
    }
    for event_type, keys in {
        "recon_started": {"recon_id", "query", "paths", "agent_count", "agent_models", "session_id", "created_at"},
        "recon_completed": {"recon_id", "status", "succeeded_agents", "failed_agents", "duration", "completed_at", "summary"},
        "task_merged": {"base_sha", "commit_count", "commits", "files_changed", "insertions", "deletions", "plan_name", "plan_wave"},
        "task_classified": {"plan_name", "plan_wave"},
        "dispatch": {"plan_name", "plan_wave"},
        "task_complete": {"plan_name", "plan_wave"},
        "halt": {"plan_name", "plan_wave"},
    }.items():
        data_schema = ingest["$defs"][by_type[event_type]["properties"]["data"]["$ref"].split("/")[-1]]
        assert keys <= set(data_schema["properties"])

    liveness = v6["cloud_liveness"]
    assert "recons" in liveness["properties"]
    plan = liveness["$defs"][liveness["properties"]["plans"]["items"]["$ref"].split("/")[-1]]
    assert "queue" in plan["properties"]

    legacy = {
        "session_id": "sess_schema", "project_path": "/work/project", "display_name": "project",
        "events": [{
            "sequence": 1, "timestamp": "2026-09-17T12:00:00+00:00", "event_type": "task_merged",
            "project_id": "local:project", "scope": "local",
            "data": {"task_ref": "1.1", "branch": "work", "merge_sha": "a" * 40, "spec": "spec", "session_id": "sess_schema"},
            "previous_hash": "0" * 64, "event_hash": "a" * 64,
        }, {
            "sequence": 2, "timestamp": "2026-09-17T12:01:00+00:00", "event_type": "plan_run",
            "project_id": "local:project", "scope": "local",
            "data": {"plan_name": "p1", "waves": [], "trigger": "cli"},
            "previous_hash": "a" * 64, "event_hash": "b" * 64,
        }],
    }
    validate(legacy, ingest)
    plan_data = by_type["plan_run"]["properties"]["data"]["$ref"].split("/")[-1]
    assert {"plan_name", "waves", "trigger", "queue", "job_id", "mode"} <= set(ingest["$defs"][plan_data]["properties"])

    v5 = publication["payloads_by_version"]["5"]["cloud_ingest"]
    v5_items = v5["properties"]["events"]["items"]
    v5_branches = v5_items.get("oneOf", v5_items.get("anyOf"))
    v5_types = {
        v5["$defs"][branch["$ref"].split("/")[-1]]["properties"]["event_type"]["const"]:
        v5["$defs"][branch["$ref"].split("/")[-1]]
        for branch in v5_branches
        if "const" in v5["$defs"][branch["$ref"].split("/")[-1]]["properties"]["event_type"]
    }
    assert "recon_started" not in v5_types
    v5_merged = v5["$defs"][v5_types["task_merged"]["properties"]["data"]["$ref"].split("/")[-1]]
    assert set(v5_merged["properties"]) == {"task_ref", "branch", "merge_sha", "spec", "session_id"}
