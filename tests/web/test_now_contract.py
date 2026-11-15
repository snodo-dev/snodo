"""Keep the Now route response and static view in sync."""

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

from snodo.web.routes_now import build_now

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "snodo" / "web" / "static" / "now.js"
NOW = 1000


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def _repository(path: Path) -> None:
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "tracked.txt").write_text("base", encoding="utf-8")
    _git(path, "add", "tracked.txt")
    _git(path, "commit", "-m", "base")


def _job(path: Path, job_id: str, task_id: str, status: str, *, plan: str = "") -> None:
    root = path / ".snodo" / "jobs" / job_id
    _write_json(root / "task.json", {"task_id": task_id, "plan_name": plan})
    _write_json(root / "state.json", {"status": status, "started_at": NOW - 60})


def _fixture(root: Path) -> dict:
    project = root / "project"
    _repository(project)
    plan = project / ".snodo" / "plans" / "delivery"
    _write_json(plan / "status.json", {"tasks": {
        "done": "completed", "active": "in_progress", "blocked": "blocked",
    }})
    (plan / "plan.yml").write_text(
        "intent: contract\nwaves:\n"
        "  - id: one\n    tasks: [done, active]\n"
        "  - id: two\n    tasks: [blocked]\n", encoding="utf-8",
    )
    os.utime(plan / "status.json", (NOW - 100, NOW - 100))
    _job(project, "job-active", "active", "running", plan="delivery")
    _job(project, "job-outside", "outside-task", "running")
    (project / "tracked.txt").write_text("uncommitted", encoding="utf-8")
    return build_now([SimpleNamespace(name="project", path=project)], NOW)


def test_route_shape_and_counters_match_real_tree(tmp_path: Path) -> None:
    result = _fixture(tmp_path)
    # These exact-key assertions guard route envelope, counter and project/plan/group/task contract fields.
    assert set(result) == {"window_hours", "generated_at", "counters", "projects", "waiting"}
    assert set(result["counters"]) == {
        "projects_active", "plans", "queues", "waves", "tasks", "recons", "commits",
    }
    assert set(result["counters"]["tasks"]) == {"count", "by_status"}
    project = result["projects"][0]
    assert set(project) == {"name", "liveness", "last_activity_at", "plans", "outside_plan"}
    assert set(project["plans"][0]) == {"name", "wave", "waves", "last_activity_at", "wave_groups"}
    group = project["plans"][0]["wave_groups"][0]
    assert set(group) == {"id", "total", "status_counts", "tasks"}
    assert set(group["tasks"][0]) == {"task_id", "status", "job", "elapsed_seconds"}
    assert set(project["outside_plan"][0]) == {"task_id", "status", "job", "elapsed_seconds"}
    # Tree has three planned tasks plus one outside-plan task; all are recent.
    assert result["counters"]["tasks"]["count"] == 4
    assert result["counters"]["tasks"]["by_status"] == {
        "completed": 1, "in_progress": 1, "blocked": 1, "running": 1,
    }
    assert result["counters"]["plans"] == 1
    assert result["counters"]["waves"] == 2
    assert result["waiting"]


def test_view_mentions_each_contract_field_it_consumes() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    # Guards envelope fields read by render/freshness.
    for field in ("window_hours", "generated_at", "counters", "projects", "waiting"):
        assert field in source
    # Guards every counter leaf consumed for tiles and task status details.
    for field in ("projects_active", "plans", "queues", "waves", "tasks", "count", "by_status", "recons", "commits"):
        assert field in source
    # Guards project, plan, wave-group, task, job, and waiting-item leaves consumed by the tree renderers.
    for field in (
        "name", "liveness", "outside_plan", "wave", "waves",
        "wave_groups", "task_id", "status",
        "job", "job_id", "elapsed_seconds", "kind", "plan", "files",
    ):
        assert field in source
