"""Tests for the read-only dashboard Plans route."""

import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from snodo.web import routes_plans


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


def _plan(project: Path, name: str, branch: str | None, tasks: dict[str, str]) -> None:
    directory = project / ".snodo" / "plans" / name
    directory.mkdir(parents=True)
    status = {"tasks": tasks, "integration": {"branch": branch} if branch else {}}
    (directory / "status.json").write_text(json.dumps(status), encoding="utf-8")
    (directory / "plan.yml").write_text(f"intent: {name} intent\n", encoding="utf-8")


def test_build_plans_progress_delivery_order_and_missing_status(tmp_path: Path) -> None:
    project = tmp_path / "alpha"
    project.mkdir()
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "test@example.com")
    _git(project, "config", "user.name", "Test")
    (project / "base.txt").write_text("base", encoding="utf-8")
    _git(project, "add", "base.txt")
    _git(project, "commit", "-m", "base")

    _git(project, "checkout", "-b", "merged-branch")
    (project / "merged.txt").write_text("merged", encoding="utf-8")
    _git(project, "add", "merged.txt")
    _git(project, "commit", "-m", "merged work")
    _git(project, "checkout", "main")
    _git(project, "merge", "--no-ff", "merged-branch", "-m", "merge")

    _git(project, "checkout", "-b", "unmerged-branch")
    (project / "unmerged.txt").write_text("unmerged", encoding="utf-8")
    _git(project, "add", "unmerged.txt")
    _git(project, "commit", "-m", "unmerged work")
    _git(project, "checkout", "main")

    _plan(project, "merged", "merged-branch", {"a": "completed"})
    time.sleep(0.02)
    _plan(project, "unmerged", "unmerged-branch", {"a": "completed", "b": "in_progress"})
    missing = project / ".snodo" / "plans" / "missing"
    missing.mkdir()
    (missing / "plan.yml").write_text("intent: missing intent\nwaves:\n  - tasks: [pending-task]\n", encoding="utf-8")

    other = tmp_path / "beta"
    other.mkdir()
    _git(other, "init", "-b", "main")
    _git(other, "config", "user.email", "test@example.com")
    _git(other, "config", "user.name", "Test")
    (other / "base.txt").write_text("base", encoding="utf-8")
    _git(other, "add", "base.txt")
    _git(other, "commit", "-m", "base")
    _plan(other, "newest", None, {"x": "completed"})

    rows = routes_plans.build_plans([
        SimpleNamespace(name="alpha", path=project),
        SimpleNamespace(name="beta", path=other),
    ])
    assert [row["name"] for row in rows] == ["newest", "unmerged", "merged", "missing"]
    assert (rows[1]["tasks_total"], rows[1]["tasks_completed"], rows[1]["running"]) == (2, 1, True)
    assert rows[1]["delivery"] == "not_in_main"
    assert rows[2]["delivery"] == "in_main"
    assert rows[-1]["updated_at"] is None


def test_handler_returns_plans_json() -> None:
    app = Starlette(routes=[Route("/plans", routes_plans.handler, methods=["GET"])])
    app.state.projects = []
    response = TestClient(app).get("/plans")
    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["content-type"].startswith("application/json")


def test_handler_collects_in_worker_thread(monkeypatch) -> None:
    import threading

    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request

    loop_thread = None
    collection_thread = None

    async def middleware(request: Request, call_next):
        nonlocal loop_thread
        loop_thread = threading.get_ident()
        return await call_next(request)

    def collect(*_args):
        nonlocal collection_thread
        collection_thread = threading.get_ident()
        return [{"unchanged": True}]

    monkeypatch.setattr(routes_plans, "build_plans", collect)
    app = Starlette(routes=[Route("/plans", routes_plans.handler, methods=["GET"])])
    app.add_middleware(BaseHTTPMiddleware, dispatch=middleware)
    app.state.projects = []

    response = TestClient(app).get("/plans")

    assert response.status_code == 200
    assert response.json() == [{"unchanged": True}]
    assert collection_thread != loop_thread
