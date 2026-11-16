"""Guarded preview and save tests for protocol editing."""

import hashlib
from pathlib import Path

import yaml
from starlette.testclient import TestClient

from snodo.web.app import create_app
from snodo.web.projects import Project


def _setup(tmp_path):
    text = Path(".snodo/protocol.yml").read_text(encoding="utf-8")
    path = tmp_path / ".snodo" / "protocol.yml"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    client = TestClient(create_app([Project("demo", tmp_path)]), base_url="http://localhost")
    payload = client.get("/api/protocol", params={"project": "demo"}).json()
    validator = yaml.safe_load(text)["validators"][0]
    return client, path, text, payload, [{"validator_id": validator["validator_id"], "phase": "post_execute"}]


def test_preview_is_non_writing_and_returns_diff(tmp_path):
    client, path, original, data, edits = _setup(tmp_path)
    response = client.post("/api/protocol/preview", json={"project": "demo", "etag": data["etag"], "edits": edits})
    assert response.status_code == 200
    assert response.json()["valid"]
    assert "--- .snodo/protocol.yml" in response.json()["diff"]
    assert response.json()["changes"]
    assert path.read_text(encoding="utf-8") == original


def test_save_returns_refreshed_payload_and_preserves_other_comments(tmp_path):
    client, path, original, data, edits = _setup(tmp_path)
    response = client.post("/api/protocol/save", json={"project": "demo", "etag": data["etag"], "edits": edits, "page_token": data["page_token"]})
    assert response.status_code == 200
    assert response.json()["etag"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert response.json()["etag"] != data["etag"]
    assert "#" in path.read_text(encoding="utf-8")
    assert original.split("validators:", 1)[0] in path.read_text(encoding="utf-8")


def test_stale_etag_and_invalid_edit_do_not_write(tmp_path):
    client, path, original, data, edits = _setup(tmp_path)
    path.write_text(original + "\n# external change\n", encoding="utf-8")
    changed = path.read_text(encoding="utf-8")
    response = client.post("/api/protocol/save", json={"project": "demo", "etag": data["etag"], "edits": edits, "page_token": data["page_token"]})
    assert response.status_code == 409
    assert path.read_text(encoding="utf-8") == changed
    etag = hashlib.sha256(path.read_bytes()).hexdigest()
    invalid = [{"validator_id": edits[0]["validator_id"], "phase": "invalid"}]
    response = client.post("/api/protocol/save", json={"project": "demo", "etag": etag, "edits": invalid, "page_token": data["page_token"]})
    assert response.status_code == 422
    assert path.read_text(encoding="utf-8") == changed


def test_post_security_guards(tmp_path):
    client, path, original, data, edits = _setup(tmp_path)
    body = {"project": "demo", "etag": data["etag"], "edits": edits, "page_token": data["page_token"]}
    assert client.post("/api/protocol/save", json={**body, "page_token": "wrong"}).status_code == 403
    assert client.post("/api/protocol/save", json=body, headers={"origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/protocol/save", content="{}", headers={"content-type": "text/plain"}).status_code == 415
    assert client.post("/api/protocol/save", json=body, headers={"host": "evil.example"}).status_code == 400
    assert client.post("/api/projects", json={}).status_code == 405
    assert path.read_text(encoding="utf-8") == original
