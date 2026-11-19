"""Tests for the read-only dashboard protocol API."""

import hashlib
from pathlib import Path

import yaml
from starlette.testclient import TestClient

from snodo.mcp.schema import build_protocol_schema_publication
from snodo.web.app import create_app
from snodo.web.projects import Project


def _client_for_protocol(tmp_path, source=None):
    project = Project("demo", tmp_path)
    protocol_path = tmp_path / ".snodo" / "protocol.yml"
    protocol_path.parent.mkdir(parents=True, exist_ok=True)
    if source is not None:
        protocol_path.write_text(source, encoding="utf-8")
    return TestClient(create_app([project]), headers={"host": "localhost"}), protocol_path


def _valid_protocol():
    source = Path(".snodo/protocol.yml").read_text(encoding="utf-8")
    protocol = yaml.safe_load(source)
    producer = protocol["modes"][0]
    producer["validators"] = ["security", "architecture"]
    second = dict(producer)
    second.update(mode_id="reviewer", name="Reviewer", tools=["read"], validators=["architecture"])
    protocol["modes"] = [producer, second]
    unused = dict(protocol["validators"][0])
    unused.update(validator_id="unused", criteria=["Not assigned to a mode"])
    protocol["validators"].append(unused)
    protocol["initial_mode"] = "producer"
    return yaml.safe_dump(protocol, sort_keys=False)


def test_protocol_api_returns_dashboard_shape_and_rules(tmp_path):
    content = _valid_protocol()
    client, path = _client_for_protocol(tmp_path, content)
    response = client.get("/api/protocol", params={"project": "demo"})
    assert response.status_code == 200
    data = response.json()
    assert data["project"] == "demo"
    assert data["path"] == str(path)
    assert data["etag"] == hashlib.sha256(content.encode()).hexdigest()
    assert data["protocol"] == {
        "id": "snodo-public", "name": "snodo-public (solo, dogfood)",
        "version": "1.0.0", "initial_mode": "producer", "effective_delivery": "local_merge", "disagreement_policy": "majority",
    }
    assert [mode["id"] for mode in data["modes"]] == ["producer", "reviewer"]
    assert data["modes"][0] == {
        "id": "producer", "name": "Producer Mode", "tools": data["modes"][0]["tools"],
        "concurrency": 1, "validators": ["security", "architecture"],
    }
    assert data["modes"][1]["validators"] == ["architecture"]
    assert [validator["id"] for validator in data["validators"]][-1] == "unused"
    unused = data["validators"][-1]
    assert unused["modes"] == []
    assert set(unused) == {"id", "type", "phase", "scope", "severity_cap", "tools", "check_tool_access", "criteria", "modes"}
    assert data["validators"][0]["phase"] == "pre_execute"
    assert data["execution"] == {
        "max_retries": 3, "branch_ttl_days": 7, "max_recovery_depth": 3,
        "max_total_fix_attempts": 10, "branch_prefix": "task", "delivery": "local_merge",
        "auto_merge": None, "parallel_runs": 1, "non_blocking": False,
    }
    rules = data["rules"]
    published = build_protocol_schema_publication()["x-snodo-cross-field-rules"]
    assert len(rules) == len(published)
    assert [rule["id"] for rule in rules] == [rule["id"] for rule in published]
    assert all(rule["ok"] is True and rule["description"] == original["description"] for rule, original in zip(rules, published))
    assert client.head("/api/protocol", params={"project": "demo"}).status_code == 200
    assert client.post("/api/protocol", params={"project": "demo"}).status_code == 405


def test_protocol_api_etag_tracks_content(tmp_path):
    client, path = _client_for_protocol(tmp_path, _valid_protocol())
    params = {"project": "demo"}
    first = client.get("/api/protocol", params=params).json()["etag"]
    assert client.get("/api/protocol", params=params).json()["etag"] == first
    path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    assert client.get("/api/protocol", params=params).json()["etag"] != first


def test_protocol_api_errors_and_methods(tmp_path):
    client, path = _client_for_protocol(tmp_path)
    params = {"project": "demo"}
    assert client.get("/api/protocol", params=params).json() == {"error": "Protocol file not found or could not be read."}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("protocol_id: [", encoding="utf-8")
    assert client.get("/api/protocol", params=params).json() == {"error": "Protocol file is invalid."}
    path.write_text("not: a valid protocol\n", encoding="utf-8")
    assert client.get("/api/protocol", params=params).json() == {"error": "Protocol file is invalid."}
    assert client.get("/api/protocol", params={"project": "unknown"}).status_code == 404


def test_protocol_api_resolves_mode_delivery_and_legacy_auto_merge(tmp_path):
    protocol = yaml.safe_load(_valid_protocol())
    protocol["execution"].pop("delivery", None)
    protocol["modes"][0]["delivery"] = "push_branch"
    client, path = _client_for_protocol(tmp_path, yaml.safe_dump(protocol, sort_keys=False))
    data = client.get("/api/protocol", params={"project": "demo"}).json()
    assert data["protocol"]["effective_delivery"] == "push_branch"
    assert data["execution"]["delivery"] is None

    protocol["modes"][0].pop("delivery")
    protocol["execution"]["auto_merge"] = False
    path.write_text(yaml.safe_dump(protocol, sort_keys=False), encoding="utf-8")
    data = client.get("/api/protocol", params={"project": "demo"}).json()
    assert data["protocol"]["effective_delivery"] == "leave_unmerged"
    assert data["execution"]["auto_merge"] is False
