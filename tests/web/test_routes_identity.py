
import pytest
from starlette.testclient import TestClient

from snodo.web import routes_identity
from snodo.web.app import create_app
from snodo.web.projects import Project


@pytest.mark.parametrize(
    ("resolved", "expected"),
    [
        (("github.com/snodo-dev/snodo", "remote"), {
            "name": "snodo", "remote": "github.com/snodo-dev/snodo",
            "project_id": "github.com/snodo-dev/snodo",
        }),
        (("local:abc123", "local"), {
            "name": "checkout", "remote": "local", "project_id": "local:abc123",
        }),
    ],
)
def test_identity_route_uses_resolved_identity(monkeypatch, tmp_path, resolved, expected):
    project_path = tmp_path / "checkout"
    project_path.mkdir()
    monkeypatch.setattr(routes_identity.project, "resolve_project_id", lambda _path: resolved)

    response = TestClient(create_app([Project("checkout", project_path)]), base_url="http://localhost").get("/api/identity")

    assert response.status_code == 200
    assert response.json() == expected


def test_identity_resolution_failure_falls_back_to_folder_and_local(monkeypatch, tmp_path):
    project_path = tmp_path / "checkout"
    project_path.mkdir()

    def fail(_path):
        raise RuntimeError("git unavailable")

    monkeypatch.setattr(routes_identity.project, "resolve_project_id", fail)
    response = TestClient(create_app([Project("checkout", project_path)]), base_url="http://localhost").get("/api/identity")

    assert response.status_code == 200
    assert response.json()["name"] == "checkout"
    assert response.json()["remote"] == "local"


def test_identity_route_does_not_modify_snodo_directory(monkeypatch, tmp_path):
    project_path = tmp_path / "checkout"
    state = project_path / ".snodo"
    state.mkdir(parents=True)
    marker = state / "keep"
    marker.write_text("unchanged")
    before = sorted((path.relative_to(state), path.read_bytes() if path.is_file() else None)
                    for path in state.rglob("*"))
    monkeypatch.setattr(routes_identity.project, "resolve_project_id", lambda _path: ("local:id", "local"))

    TestClient(create_app([Project("checkout", project_path)]), base_url="http://localhost").get("/api/identity")

    after = sorted((path.relative_to(state), path.read_bytes() if path.is_file() else None)
                   for path in state.rglob("*"))
    assert after == before
