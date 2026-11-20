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
    _write_json(plan / "status.json", {
        "tasks": {"done": "completed", "active": "in_progress", "blocked": "blocked"},
        "integration": {"branch": "plan/delivery"},
    })
    (plan / "plan.yml").write_text(
        "intent: contract\nwaves:\n"
        "  - id: one\n    tasks: [done, active]\n"
        "  - id: two\n    tasks: [blocked]\n", encoding="utf-8",
    )
    os.utime(plan / "status.json", (NOW - 100, NOW - 100))
    waiting_plan = project / ".snodo" / "plans" / "waiting"
    _write_json(waiting_plan / "status.json", {
        "tasks": {"shipped": "completed"},
        "integration": {"branch": "plan/delivery"},
    })
    (waiting_plan / "plan.yml").write_text(
        "intent: waiting\nwaves:\n  - id: one\n    tasks: [shipped]\n", encoding="utf-8",
    )
    _git(project, "checkout", "-b", "plan/delivery")
    (project / "tracked.txt").write_text("branch change", encoding="utf-8")
    _git(project, "commit", "-am", "branch change")
    _git(project, "checkout", "main")
    _job(project, "job-active", "active", "running", plan="delivery")
    _job(project, "job-outside", "outside-task", "running")
    (project / "tracked.txt").write_text("uncommitted", encoding="utf-8")
    return build_now([SimpleNamespace(name="project", path=project)], NOW)


def test_route_shape_and_counters_match_real_tree(tmp_path: Path) -> None:
    result = _fixture(tmp_path)
    # Exact-key assertions guard each route envelope/counter/project/plan/group/task field.
    assert set(result) == {"window_hours", "generated_at", "counters", "projects", "waiting"}
    counters = result["counters"]
    assert set(counters) == {"projects_active", "plans", "queues", "waves", "tasks", "recons", "commits"}
    assert set(counters["tasks"]) == {"count", "by_status"}
    assert counters["recons"] == {"count": 0, "running": 0, "completed": 0}
    project = result["projects"][0]
    assert set(project) == {"name", "liveness", "last_activity_at", "plans", "outside_plan"}
    assert len(project["plans"]) == 2
    plan_row = next(plan for plan in project["plans"] if plan["name"] == "delivery")
    assert all(set(plan) == {"name", "wave", "waves", "last_activity_at", "wave_groups"} for plan in project["plans"])
    group = plan_row["wave_groups"][0]
    assert set(group) == {"id", "total", "status_counts", "tasks"}
    assert set(group["tasks"][0]) == {"task_id", "status", "job", "elapsed_seconds"}
    planned_rows = [task for wave in plan_row["wave_groups"] for task in wave["tasks"]]
    assert next(task for task in planned_rows if task["task_id"] == "active")["job"] == {"job_id": "job-active"}
    assert set(project["outside_plan"][0]) == {"task_id", "status", "job", "elapsed_seconds"}
    assert project["outside_plan"][0]["job"] == {"job_id": "job-outside"}
    # These exact-key assertions guard every waiting-item field, including both variants.
    assert result["waiting"] == [
        {"kind": "not_in_main", "project": "project", "plan": "waiting"},
        {"kind": "uncommitted", "project": "project", "files": ["tracked.txt"]},
    ]
    assert set(result["waiting"][0]) == {"kind", "project", "plan"}
    assert set(result["waiting"][1]) == {"kind", "project", "files"}
    # Tree has four planned tasks plus one outside-plan task; all are recent.
    assert result["counters"]["tasks"]["count"] == 5
    assert result["counters"]["tasks"]["by_status"] == {
        "completed": 2, "in_progress": 1, "blocked": 1, "running": 1,
    }
    assert result["counters"]["plans"] == 2
    assert result["counters"]["waves"] == 3
    assert result["waiting"]


def test_view_mentions_each_contract_field_it_consumes() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    # Each expression assertion guards an actual property read, not a coincidental CSS/class substring.
    for expression in (
        "data.window_hours", "data.generated_at", "data.counters", "data.projects", "data.waiting",
        "counters.projects_active", "counters.plans", "counters.queues", "counters.waves",
        "counters.tasks", "counters.recons", "counters.commits", "counters.tasks.by_status",
        "counters.tasks.count", "recons.running", "recons.completed", "recons.count",
        "project.name", "project.liveness", "project.plans", "project.outside_plan",
        "plan.name", "plan.wave", "plan.waves", "plan.wave_groups", "group.tasks",
        "task.task_id", "task.status", "task.job", "task.elapsed_seconds", "task.job.job_id",
        "item.kind", "item.plan", "item.files",
    ):
        assert expression in source
