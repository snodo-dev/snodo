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

from snodo.web.flow_types import task_flow_types
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


def blocked_attempts(task_dir: Path) -> list[dict[str, Any]]:
    """Return state/archived-attempt records with a blocked halt."""
    records = [_read(task_dir / "state.json")]
    try:
        records.extend(_read(path) for path in (task_dir / "attempts").glob("*.json"))
    except OSError:
        pass
    return [item for item in records
            if isinstance(item.get("halt"), dict) and item["halt"].get("status") == "blocked"]


def _was_sent_back(task_dir: Path) -> bool:
    return bool(blocked_attempts(task_dir))


def _send_back_moment(root: Path, task_id: str) -> tuple[bool, datetime | None]:
    """Return whether history is known and the earliest recorded send-back."""
    task_dir = root / ".snodo" / "tasks" / task_id
    moments: list[datetime] = []
    known = False
    records = [_read(task_dir / "state.json")]
    try:
        records.extend(_read(path) for path in (task_dir / "attempts").glob("*.json"))
    except OSError:
        pass
    for record in records:
        halt = record.get("halt")
        if not isinstance(halt, dict):
            continue
        if isinstance(halt.get("attempts"), dict) and isinstance(halt["attempts"].get("total"), (int, float)):
            known = True
        if halt.get("status") == "blocked":
            known = True
            stamp = _timestamp(record.get("completed_at"))
            if stamp is None:
                return False, None
            moments.append(stamp)
    try:
        fix_dirs = [path for path in (root / ".snodo" / "tasks").iterdir()
                    if path.is_dir() and path.name.startswith(f"{task_id}_fix_")]
    except OSError:
        fix_dirs = []
    for fix_dir in fix_dirs:
        known = True
        stamp = _timestamp(_read(fix_dir / "state.json").get("started_at"))
        if stamp is None:
            return False, None
        moments.append(stamp)
    return known, min(moments) if moments else None


def flow_metrics(project_path: str | Path, window_days: int, now: datetime | None = None) -> dict[str, Any]:
    """Compute landed throughput, duration, first-try rate, and active load."""
    root = Path(project_path)
    empty = {"landed": 0, "landed_by_day": [], "median_flow_seconds": None,
             "first_try_rate": None, "flow_load": 0, "efficiency": None, "efficiency_tasks": 0}
    empty.update(_empty_type_metrics())
    current_for_weeks = now or datetime.now(timezone.utc)
    if current_for_weeks.tzinfo is None:
        current_for_weeks = current_for_weeks.replace(tzinfo=timezone.utc)
    first_for_weeks = (current_for_weeks.date() - timedelta(days=max(0, int(window_days)) - 1))
    empty["by_week"] = _week_zeros(first_for_weeks, current_for_weeks.date())
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

    flow_types = task_flow_types(root)
    type_counts = {kind: 0 for kind in _FLOW_KINDS}
    type_durations: dict[str, list[float]] = {kind: [] for kind in type_counts}
    unclassified = 0
    by_week = _week_zeros(first_day, current.date())
    weeks = {row["week_start"]: row for row in by_week}
    for task, completed in landed_stamps:
        task_id = task.get("task_id", "")
        kind = flow_types.get(task_id)
        week = weeks[(completed.date() - timedelta(days=completed.weekday())).isoformat()]
        if kind not in type_counts:
            unclassified += 1
            week["unclassified"] += 1
            continue
        type_counts[kind] += 1
        week[kind] += 1
        started = _timestamp(states.get(task_id, {}).get("started_at"))
        if started:
            type_durations[kind].append(max(0.0, (completed - started).total_seconds()))

    by_type = _type_summary(type_counts, type_durations)
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
    efficiency_flow = 0.0
    efficiency_rework = 0.0
    efficiency_tasks = 0
    for task, completed in landed_stamps:
        task_id = task.get("task_id", "")
        started = _timestamp(states.get(task_id, {}).get("started_at"))
        if started is None:
            continue
        known, send_back = _send_back_moment(root, task_id)
        if not known:
            continue
        flow_seconds = max(0.0, (completed - started).total_seconds())
        rework_seconds = (0.0 if send_back is None else
                          min(flow_seconds, max(0.0, (completed - send_back).total_seconds())))
        efficiency_flow += flow_seconds
        efficiency_rework += rework_seconds
        efficiency_tasks += 1
    efficiency = (1.0 - efficiency_rework / efficiency_flow if efficiency_flow else
                  (1.0 if efficiency_tasks else None))
    return {
        "landed": len(landed_stamps),
        "landed_by_day": [{"date": day, "landed": count} for day, count in by_day.items()],
        "median_flow_seconds": median(durations) if durations else None,
        "first_try_rate": first_try_rate,
        "efficiency": efficiency,
        "efficiency_tasks": efficiency_tasks,
        "_efficiency_flow_seconds": efficiency_flow,
        "_efficiency_rework_seconds": efficiency_rework,
        "flow_load": sum(task.get("status") == "in_progress" for task in tasks),
        "by_type": by_type,
        "unclassified": unclassified,
        "by_week": by_week,
        "_flow_seconds_by_type": type_durations,
        "_flow_seconds": durations,
    }


_FLOW_KINDS = ("feature", "defect", "debt", "risk")


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _type_summary(counts: dict[str, int], durations: dict[str, list[float]]) -> dict[str, Any]:
    classified = sum(counts.values())
    return {
        kind: {
            "landed": counts[kind],
            "share": counts[kind] / classified if classified else None,
            "median_flow_seconds": median(durations[kind]) if durations[kind] else None,
            "p85_flow_seconds": _percentile(durations[kind], 0.85),
        }
        for kind in _FLOW_KINDS
    }


def _week_zeros(first_day, last_day) -> list[dict[str, Any]]:
    monday = first_day - timedelta(days=first_day.weekday())
    result = []
    while monday <= last_day:
        result.append({"week_start": monday.isoformat(), **{kind: 0 for kind in _FLOW_KINDS}, "unclassified": 0})
        monday += timedelta(days=7)
    return result


def _empty_type_metrics() -> dict[str, Any]:
    counts = {kind: 0 for kind in _FLOW_KINDS}
    durations = {kind: [] for kind in _FLOW_KINDS}
    return {"by_type": _type_summary(counts, durations), "unclassified": 0,
            "by_week": [], "_flow_seconds_by_type": durations, "_flow_seconds": []}
