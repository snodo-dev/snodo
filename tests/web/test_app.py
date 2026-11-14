from pathlib import Path

import pytest
from starlette.testclient import TestClient

from snodo.web import app as dashboard_app
from snodo.web.projects import Project


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(dashboard_app, "_STATIC_DIR", Path(__file__).parent / "missing-static")
    with TestClient(
        dashboard_app.create_app([Project("demo", Path("/tmp/demo"))]),
        base_url="http://localhost",
    ) as test_client:
        yield test_client


@pytest.mark.parametrize("path", [
    "/api/projects", "/api/now", "/api/plans", "/api/delivery", "/api/gates",
])
def test_api_paths_return_json(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")


def test_index_placeholder_when_missing(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "missing" in response.text


def test_index_serves_file(monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text("dashboard")
    monkeypatch.setattr(dashboard_app, "_STATIC_DIR", tmp_path)
    response = TestClient(dashboard_app.create_app([]), base_url="http://localhost").get("/")
    assert response.status_code == 200
    assert response.text == "dashboard"


def test_post_is_not_allowed(client):
    assert client.post("/api/projects").status_code == 405


@pytest.mark.parametrize("host", ["evil.example"])
def test_rejects_non_loopback_host(client, host):
    assert client.get("/api/projects", headers={"host": host}).status_code == 400


@pytest.mark.parametrize("host", ["localhost:55441", "127.0.0.1"])
def test_accepts_loopback_host(client, host):
    assert client.get("/api/projects", headers={"host": host}).status_code == 200


def test_default_port_picks_first_free():
    assert dashboard_app._select_port(lambda port: port >= 55444) == 55444


def test_default_port_fails_when_all_taken():
    with pytest.raises(RuntimeError, match="55441-55460"):
        dashboard_app._select_port(lambda _port: False)


def test_explicit_taken_port_fails():
    with pytest.raises(RuntimeError, match="55441.*in use"):
        dashboard_app._select_port(lambda _port: False, 55441)
