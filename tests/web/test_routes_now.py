"""Tests for the read-only dashboard Now route."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.testclient import TestClient
from starlette.routing import Route

from snodo.web.routes_now import build_now, handler


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def repo(path: Path) -> None:
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    (path / "tracked.txt").write_text("base")
    git(path, "add", "tracked.txt")
    git(path, "commit", "-m", "base")


def seed_plan(path: Path, name: str, tasks: dict, waves: list | None = None) -> None:
    root = path / ".snodo" / "plans" / name
    write_json(root / "status.json", {"tasks": tasks})
    wave_text = "".join(
        f"  - id: {wave['id']}\n    tasks: [{', '.join(wave['tasks'])}]\n"
        for wave in (waves or [])
    )
    (root / "plan.yml").write_text("intent: test\nwaves:\n" + wave_text, encoding="utf-8")


def seed_job(path: Path, job_id: str, task_id: str, started_at: float) -> None:
    root = path / ".snodo" / "jobs" / job_id
    write_json(root / "task.json", {"task_id": task_id})
    write_json(root / "state.json", {"status": "running", "started_at": started_at})


def test_running_plan_lists_job_and_idle_project_has_no_plans(tmp_path: Path) -> None:
    active = tmp_path / "active"
    repo(active)
    seed_plan(active, "build", {"task-1": "in_progress"}, [{"id": "one", "tasks": ["task-1"]}])
    seed_job(active, "job-1", "task-1", 900)
    quiet = tmp_path / "quiet"
    repo(quiet)

    result = build_now([
        SimpleNamespace(name="active", path=active),
        SimpleNamespace(name="quiet", path=quiet),
    ], 1000)

    assert result["projects"][0] == {
        "name": "active", "state": "working", "plans": [{
            "name": "build", "wave": 1, "waves": 1,
            "tasks": [{"task_id": "task-1", "status": "in_progress", "job": {
                "job_id": "job-1", "status": "running", "elapsed_seconds": 100,
            }}],
        }],
    }
    assert result["projects"][1] == {"name": "quiet", "state": "idle", "plans": []}


def test_finished_unmerged_plan_and_tracked_modifications_wait(tmp_path: Path) -> None:
    path = tmp_path / "repo"
    repo(path)
    seed_plan(path, "finished", {"task-1": "completed"})
    write_json(path / ".snodo" / "plans" / "finished" / "status.json", {
        "tasks": {"task-1": "completed"}, "integration": {"branch": "plan/finished"},
    })
    git(path, "checkout", "-b", "plan/finished")
    (path / "tracked.txt").write_text("branch change")
    git(path, "commit", "-am", "branch change")
    git(path, "checkout", "main")
    (path / "tracked.txt").write_text("modified")
    (path / "untracked.txt").write_text("untracked")

    result = build_now([SimpleNamespace(name="repo", path=path)], 1000)

    assert {item["kind"] for item in result["waiting"]} == {"not_in_main", "uncommitted"}
    uncommitted = next(item for item in result["waiting"] if item["kind"] == "uncommitted")
    assert uncommitted["files"] == ["tracked.txt"]


def test_unreadable_project_does_not_break_response(tmp_path: Path) -> None:
    result = build_now([SimpleNamespace(name="broken", path=tmp_path / "missing")], 1000)
    assert result == {"projects": [{"name": "broken", "state": "idle", "plans": []}], "waiting": []}


def test_handler_returns_json_200(tmp_path: Path) -> None:
    application = Starlette(routes=[Route("/now", handler, methods=["GET"])])
    application.state.projects = [SimpleNamespace(name="empty", path=tmp_path / "absent")]

    response = TestClient(application).get("/now")

    assert response.status_code == 200
    assert response.json() == {
        "projects": [{"name": "empty", "state": "idle", "plans": []}], "waiting": [],
    }
