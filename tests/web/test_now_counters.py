"""Tests for local Now counters."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from snodo.web.now_counters import UNAVAILABLE, project_now_counters


NOW = 1_700_000_000


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _git(path: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True, env=env).strip()


def _repo(path: Path) -> None:
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "tracked.txt").write_text("base", encoding="utf-8")
    _git(path, "add", "tracked.txt")
    _commit(path, "base", NOW - 3 * 86400)


def _commit(path: Path, message: str, timestamp: int) -> None:
    env = os.environ.copy()
    date = f"@{timestamp} +0000"
    env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    _git(path, "commit", "--allow-empty", "-m", message, env=env)


def _plan(path: Path, name: str, tasks: dict[str, object], waves: list[dict]) -> Path:
    plan = path / ".snodo" / "plans" / name
    _write_json(plan / "status.json", {"tasks": tasks})
    wave_lines = "".join(
        f"  - id: {wave['id']}\n    tasks: [{', '.join(wave['tasks'])}]\n" for wave in waves
    )
    (plan / "plan.yml").write_text("intent: test\nwaves:\n" + wave_lines, encoding="utf-8")
    return plan


def _job(path: Path, job_id: str, task_id: str, status: str, timestamp: int, plan: str | None = None) -> None:
    job = path / ".snodo" / "jobs" / job_id
    task = {"task_id": task_id}
    if plan is not None:
        task["plan_name"] = plan
    _write_json(job / "task.json", task)
    _write_json(job / "state.json", {"status": status, "started_at": timestamp})


def _snapshot(root: Path) -> dict[str, tuple[str, int]]:
    return {
        str(path.relative_to(root)): (path.read_bytes().hex(), path.stat().st_mtime_ns)
        for path in root.rglob("*") if path.is_file()
    }


def test_counts_recent_plan_tasks_waves_outside_tasks_and_commits_read_only(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _repo(project)
    _plan(project, "recent", {"task-a": "completed", "task-b": "blocked"}, [
        {"id": "one", "tasks": ["task-a"]}, {"id": "two", "tasks": ["task-b"]},
    ])
    old = _plan(project, "old", {"old-task": "in_progress"}, [{"id": "old-wave", "tasks": ["old-task"]}])
    os.utime(old / "status.json", (NOW - 2 * 86400, NOW - 2 * 86400))
    _write_json(project / ".snodo" / "queues.json", {"recent": "queue-a", "old": "queue-b"})
    _job(project, "recent-job", "task-a", "completed", NOW - 60, "recent")
    _job(project, "outside-job", "standalone", "running", NOW - 30)
    _commit(project, "recent commit", NOW - 60)
    _commit(project, "old commit", NOW - 2 * 86400)
    before = _snapshot(project)

    result = project_now_counters(project, NOW)

    assert result["active"] is True
    assert result["plans_queues"] == {"plans": 1, "queues": 1}
    assert result["waves"] == 2
    assert result["tasks"] == {"total": 3, "by_status": {"completed": 1, "blocked": 1, "running": 1}}
    assert result["commits"] == 1
    assert result["recons"] == UNAVAILABLE
    assert _snapshot(project) == before


def test_missing_project_is_unavailable(tmp_path: Path) -> None:
    result = project_now_counters(tmp_path / "missing", NOW)
    assert all(value == UNAVAILABLE for value in result.values())


def test_missing_project_has_unavailable_figures(tmp_path: Path) -> None:
    result = project_now_counters(tmp_path / "missing", NOW)
    assert all(value == UNAVAILABLE for value in result.values())


def test_unreadable_project_returns_unavailable(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    _repo(project)
    (project / ".snodo" / "plans").mkdir(parents=True)
    original_iterdir = Path.iterdir

    def unreadable(path: Path):
        if path == project / ".snodo" / "plans":
            raise PermissionError("permission denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", unreadable)
    result = project_now_counters(project, NOW)

    assert all(value == UNAVAILABLE for value in result.values())
