"""Tests for the read-only Now activity tree."""

from __future__ import annotations

import json
import os
from pathlib import Path

from snodo.web.now_tree import project_now_tree

NOW = 1_700_000_000


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _plan(root: Path, name: str, tasks: dict[str, str], waves: list[list[str]], stamp: int) -> None:
    directory = root / ".snodo" / "plans" / name
    _json(directory / "status.json", {"tasks": tasks})
    lines = "intent: test\nwaves:\n" + "".join(
        f"  - id: w{i}\n    tasks: [{', '.join(wave)}]\n" for i, wave in enumerate(waves, 1)
    )
    (directory / "plan.yml").write_text(lines, encoding="utf-8")
    os.utime(directory / "status.json", (stamp, stamp))


def _job(root: Path, jid: str, tid: str, status: str, start: int, plan: str | None = None) -> None:
    task = {"task_id": tid}
    if plan:
        task["plan_name"] = plan
    _json(root / ".snodo" / "jobs" / jid / "task.json", task)
    state = {"status": status, "started_at": start}
    _json(root / ".snodo" / "jobs" / jid / "state.json", state)


def test_recent_tree_status_counts_running_and_outside_plan(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _plan(root, "done", {"a": "completed", "b": "blocked"}, [["a"], ["b"]], NOW - 3600)
    _plan(root, "old", {"z": "completed"}, [["z"]], NOW - 2 * 86400)
    _job(root, "run", "c", "running", NOW - 42)
    _json(root / ".snodo" / "jobs" / "run" / "task.json", {"task_id": "c"})
    result = project_now_tree(root, NOW)
    assert result["liveness"] == "working"
    assert [plan["name"] for plan in result["plans"]] == ["done"]
    assert result["plans"][0]["waves"][1]["status_counts"] == {"blocked": 1}
    assert result["plans"][0]["waves"][1]["tasks"][0]["status"] == "blocked"
    assert result["outside_plan"][0]["elapsed_seconds"] == 42
    assert "old" not in [p["name"] for p in result["plans"]]


def test_recent_idle_and_missing_projects(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _plan(root, "done", {"a": "completed"}, [["a"]], NOW - 10)
    assert project_now_tree(root, NOW)["liveness"] == "reporting_not_progressing"
    assert project_now_tree(tmp_path / "missing", NOW)["liveness"] == "no_recent_signal"


def test_unreadable_project_does_not_raise(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "project"
    _plan(root, "plan", {"a": "completed"}, [["a"]], NOW - 10)
    original = Path.iterdir

    def unreadable(path: Path):
        if path == root / ".snodo" / "plans":
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", unreadable)
    result = project_now_tree(root, NOW)
    assert result["liveness"] == "no_recent_signal"
    assert result["plans"] == []
