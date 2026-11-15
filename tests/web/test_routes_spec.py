from pathlib import Path

import pytest
from starlette.testclient import TestClient

from snodo.web.app import create_app
from snodo.web.projects import Project


@pytest.fixture
def spec_client(tmp_path: Path):
    project_path = tmp_path / "demo"
    spec = project_path / ".snodo" / "plans" / "plan-1" / "wave_1" / "task-1_task.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# Task details\n\nDo the work.\n")
    client = TestClient(
        create_app([Project("demo", project_path)]), base_url="http://localhost"
    )
    return client, spec


def test_returns_task_spec(spec_client):
    client, _ = spec_client
    response = client.get(
        "/api/now/spec", params={"project": "demo", "plan": "plan-1", "task": "task-1"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "project": "demo",
        "plan": "plan-1",
        "task": "task-1",
        "markdown": "# Task details\n\nDo the work.\n",
    }


@pytest.mark.parametrize("params", [
    {"project": "missing", "plan": "plan-1", "task": "task-1"},
    {"project": "demo", "plan": "missing", "task": "task-1"},
    {"project": "demo", "plan": "plan-1", "task": "missing"},
    {"project": "demo", "plan": "../outside", "task": "task-1"},
    {"project": "demo", "plan": "plan-1", "task": "../outside"},
    {"project": "demo", "plan": "dir/plan", "task": "task-1"},
    {"project": "demo", "plan": "plan-1", "task": "dir/task"},
])
def test_unknown_or_unsafe_names_return_404(spec_client, params):
    client, _ = spec_client
    assert client.get("/api/now/spec", params=params).status_code == 404


@pytest.mark.parametrize("params", [
    {}, {"project": "demo", "plan": "plan-1"},
    {"project": "demo", "task": "task-1"},
    {"plan": "plan-1", "task": "task-1"},
])
def test_missing_value_returns_400(spec_client, params):
    client, _ = spec_client
    response = client.get("/api/now/spec", params=params)
    assert response.status_code == 400
    assert "error" in response.json()


def test_unsafe_names_do_not_read_outside_plan_folders(spec_client):
    client, spec = spec_client
    outside = spec.parents[4] / "outside_task.md"
    outside.write_text("must not be read")
    response = client.get(
        "/api/now/spec", params={"project": "demo", "plan": "..", "task": "outside"}
    )
    assert response.status_code == 404
    assert "must not be read" not in response.text


def test_post_is_not_allowed(spec_client):
    client, _ = spec_client
    assert client.post("/api/now/spec").status_code == 405


def test_route_is_registered():
    app = create_app([])
    assert any(getattr(route, "path", None) == "/api/now/spec" for route in app.routes)
