"""Tests for the read-only dashboard protocol API."""

from starlette.testclient import TestClient

from snodo.web.app import create_app
from snodo.web.projects import Project


def test_protocol_api_errors_and_methods(tmp_path):
    project = Project("demo", tmp_path)
    client = TestClient(create_app([project]), headers={"host": "localhost"})
    response = client.get("/api/protocol", params={"project": "demo"})
    assert response.status_code == 400
    assert response.json() == {"error": "Protocol file not found or could not be read."}
    assert client.get("/api/protocol", params={"project": "unknown"}).status_code == 404
    assert client.post("/api/protocol", params={"project": "demo"}).status_code == 405
    assert client.head("/api/protocol", params={"project": "demo"}).status_code == 400
