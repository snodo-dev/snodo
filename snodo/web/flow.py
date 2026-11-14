"""Read-only project flow metrics.

Landing status and task identities come from plan ``status.json`` via
``DashboardDataProvider.get_tasks``. Landing/start timestamps come from
``.snodo/tasks/<task>/state.json`` (completion falls back to matching job
state records); elapsed times are included only when both are recorded.
First-try eligibility comes from archived task attempts: a ``blocked`` halt
in state/attempts marks a gate send-back. Missing attempt records are unknown,
not assumed first-try successes. Flow load is the count of ``in_progress``
plan task statuses.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from statistics import median
from typing import Any

from snodo.dashboard.providers import DashboardDataProvider


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _timestamp(value: Any) -> datetime | None:
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc)
        if isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (OverflowError, OSError, ValueError):
        pass
    return None


def _task_records(root: Path) -> dict[str, dict[str, Any]]:
    result = {}
    tasks_dir = root / ".snodo" / "tasks"
    try:
        paths = [path for path in tasks_dir.iterdir() if path.is_dir()]
    except OSError:
        return result
    for path in paths:
        if "_fix_" not in path.name:
            result[path.name] = _read(path / "state.json")
    return result


def _was_sent_back(task_dir: Path) -> bool:
    records = [_read(task_dir / "state.json")]
    attempts = task_dir / "attempts"
    try:
        records.extend(_read(path) for path in attempts.glob("*.json"))
    except OSError:
        pass
    return any(isinstance(item.get("halt"), dict) and item["halt"].get("status") == "blocked" for item in records)


def flow_metrics(project_path: str | Path, window_days: int, now: datetime | None = None) -> dict[str, Any]:
    """Compute landed throughput, duration, first-try rate, and active load."""
    root = Path(project_path)
    empty = {"landed": 0, "landed_by_day": [], "median_flow_seconds": None,
             "first_try_rate": None, "flow_load": 0}
    if not (root / ".snodo").is_dir():
        return empty

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    days = max(0, int(window_days))
    first_day = (current - timedelta(days=days - 1)).date() if days else current.date()
    start = datetime.combine(first_day, datetime.min.time(), timezone.utc)
    tasks = DashboardDataProvider(str(root)).get_tasks("")
    statuses = [task for task in tasks if task.get("status") == "completed"]
    states = _task_records(root)

    completion_by_task: dict[str, datetime] = {}
    job_dir = root / ".snodo" / "jobs"
    try:
        for job in job_dir.iterdir():
            if not job.is_dir():
                continue
            task_id = _read(job / "task.json").get("task_id") or _read(job / "task.json").get("retry_task_id")
            stamp = _timestamp(_read(job / "state.json").get("completed_at"))
            if task_id and stamp and (task_id not in completion_by_task or stamp > completion_by_task[task_id]):
                completion_by_task[task_id] = stamp
    except OSError:
        pass

    landed_stamps: list[tuple[dict[str, Any], datetime]] = []
    for task in statuses:
        task_id = task.get("task_id", "")
        state = states.get(task_id, {})
        completed = _timestamp(state.get("completed_at")) or completion_by_task.get(task_id)
        if completed and start <= completed <= current:
            landed_stamps.append((task, completed))

    by_day = { (first_day + timedelta(days=i)).isoformat(): 0 for i in range(days) }
    for _, stamp in landed_stamps:
        key = stamp.date().isoformat()
        if key in by_day:
            by_day[key] += 1

    durations = []
    for task, completed in landed_stamps:
        state = states.get(task.get("task_id", ""), {})
        started = _timestamp(state.get("started_at"))
        if started:
            durations.append(max(0.0, (completed - started).total_seconds()))

    sent_back = {task_id for task_id, state in states.items() if _was_sent_back(root / ".snodo" / "tasks" / task_id)}
    known_attempts = []
    for task, _ in landed_stamps:
        state = states.get(task.get("task_id", ""), {})
        halt = state.get("halt")
        attempts = halt.get("attempts") if isinstance(halt, dict) else None
        if isinstance(attempts, dict) and isinstance(attempts.get("total"), (int, float)):
            known_attempts.append(task)
        elif _was_sent_back(root / ".snodo" / "tasks" / task.get("task_id", "")):
            known_attempts.append(task)
    first_try_rate = (sum(task.get("task_id") not in sent_back for task in known_attempts) / len(known_attempts)
                      if known_attempts else None)
    return {
        "landed": len(landed_stamps),
        "landed_by_day": [{"date": day, "landed": count} for day, count in by_day.items()],
        "median_flow_seconds": median(durations) if durations else None,
        "first_try_rate": first_try_rate,
        "flow_load": sum(task.get("status") == "in_progress" for task in tasks),
    }
