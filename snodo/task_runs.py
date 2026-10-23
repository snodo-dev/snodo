"""Read and render the jobs that ran a task."""

import json
import re
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def task_runs(project_root: str | Path, task_id: str) -> list[dict[str, Any]]:
    """Return runs of *task_id* and its recovery fixes, oldest first."""
    root = Path(project_root)
    tasks_dir = root / ".snodo" / "tasks"
    ids = {task_id}
    if tasks_dir.is_dir():
        ids.update(
            path.name for path in tasks_dir.iterdir()
            if path.is_dir() and re.fullmatch(re.escape(task_id) + r"_fix_\d+", path.name)
        )

    rows: dict[tuple[str, str], dict[str, Any]] = {}

    def add(job_id: str, run_id: str, task: dict, state: dict) -> None:
        key = (job_id, run_id)
        status = state.get("status") or "unknown"
        started = state.get("started_at") or state.get("created_at")
        ended = state.get("completed_at")
        rows[key] = {
            "job_id": job_id,
            "plan_name": task.get("plan_name") or state.get("plan_name"),
            "task_wave": task.get("task_wave") or state.get("task_wave"),
            "task_id": run_id,
            "started_at": started,
            "ended_at": ended if ended is not None else ("running" if status in ("running", "queued") else None),
            "outcome": status,
        }

    # Durable task records capture job IDs, including attempts archived on retry.
    for run_id in ids:
        task_dir = tasks_dir / run_id
        records = [_read_json(task_dir / "state.json")]
        attempts = task_dir / "attempts"
        if attempts.is_dir():
            records.extend(_read_json(path) for path in sorted(attempts.glob("*.json")))
        for record in records:
            job_id = record.get("job_id")
            if isinstance(job_id, str) and job_id:
                job_dir = root / ".snodo" / "jobs" / job_id
                add(job_id, run_id, _read_json(job_dir / "task.json"), _read_json(job_dir / "state.json") or record)
                # Task-level outcome/time is more precise for plan tasks sharing one job.
                add(job_id, run_id, _read_json(job_dir / "task.json"), record)

    jobs_dir = root / ".snodo" / "jobs"
    if jobs_dir.is_dir():
        for job_dir in jobs_dir.iterdir():
            if not job_dir.is_dir():
                continue
            task = _read_json(job_dir / "task.json")
            state = _read_json(job_dir / "state.json")
            matched = {value for value in (task.get("task_id"), task.get("retry_task_id")) if value in ids}
            halt = state.get("halt") if isinstance(state.get("halt"), dict) else {}
            if halt.get("task_id") in ids:
                matched.add(halt["task_id"])
            for run_id in matched:
                add(job_dir.name, run_id, task, state)

    def order(row: dict[str, Any]) -> tuple[int, Any, str, str]:
        started = row["started_at"]
        if isinstance(started, (int, float)):
            return 0, started, row["job_id"], row["task_id"]
        return 1, str(started or ""), row["job_id"], row["task_id"]

    return sorted(rows.values(), key=order)


def task_runs_data(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return stable plain dictionaries suitable for JSON serialization."""
    keys = ("job_id", "plan_name", "task_wave", "task_id", "started_at", "ended_at", "outcome")
    return [{key: run.get(key) for key in keys} for run in runs]


def render_task_runs(runs: list[dict[str, Any]]) -> None:
    """Print a Rich run table; emit nothing for an empty list."""
    if not runs:
        return
    from rich.console import Console
    from rich.table import Table

    table = Table(title="Task runs")
    for column in ("JOB", "PLAN/WAVE", "TASK", "STARTED", "ENDED", "OUTCOME"):
        table.add_column(column)
    for run in runs:
        plan = run.get("plan_name") or ""
        wave = run.get("task_wave")
        table.add_row(
            run["job_id"], f"{plan}/{wave}" if plan and wave is not None else plan,
            run["task_id"], str(run.get("started_at") or ""),
            str(run.get("ended_at") or ""), str(run.get("outcome") or ""),
        )
    Console().print(table)
