"""Tests for the published cloud schema ratchet."""

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "enforce_cloud_schema.py"
spec = importlib.util.spec_from_file_location("enforce_cloud_schema", SCRIPT_PATH)
schema_check = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(schema_check)


def test_published_schemas_match_baseline():
    baseline = json.loads(
        (REPO_ROOT / "scripts" / "cloud_schema_baseline.json").read_text(),
    )
    report = schema_check.check(schema_check.published_schemas(), baseline)
    assert report.added == []
    assert report.removed == []


def test_declared_payload_field_change_fails_and_names_field(monkeypatch):
    from snodo.infrastructure.cloud_liveness import LivenessSnapshot

    baseline = json.loads(
        (REPO_ROOT / "scripts" / "cloud_schema_baseline.json").read_text(),
    )
    annotations = dict(LivenessSnapshot.__annotations__)
    annotations["new_published_field"] = str
    monkeypatch.setattr(LivenessSnapshot, "__annotations__", annotations)

    report = schema_check.check(schema_check.published_schemas(), baseline)

    assert ("cloud_liveness", "new_published_field") in report.added
