"""Read-only last-24-hour counters for one local Snodo project.

Plan/task movement uses job ``state.json`` started/completed/created timestamps
and, as a fallback for plans without jobs, plan/status file modification times.
Commits use local git commit timestamps across all refs. No recon-run records
are persisted/read by the local dashboard, so recons is explicitly unavailable.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from snodo.tools.git import open_repo

UNAVAILABLE = {"available": False, "reason": "unavailable"}


def _timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def project_now_counters(project_path: str | Path, now: datetime | float) -> dict[str, Any]:
    """Calculate one project's activity counters without modifying its files."""
    if isinstance(now, datetime):
        cutoff = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).timestamp() - 86400
    else:
        cutoff = float(now) - 86400
    root = Path(project_path)
    snodo = root / ".snodo"
    try:
        if not root.is_dir() or not snodo.is_dir():
            raise OSError("project directory unavailable")
        plans_dir = snodo / "plans"
        queue_file = snodo / "queues.json"
        queues_data = _read_json(queue_file) if queue_file.exists() else None
        queue_map = queues_data if isinstance(queues_data, dict) else {}
        plan_dirs = [p for p in plans_dir.iterdir() if p.is_dir()] if plans_dir.is_dir() else []
        recent_plans: set[str] = set()
        task_statuses: Counter[str] = Counter()
        all_tasks: dict[str, tuple[str, str]] = {}
        for plan in plan_dirs:
            status_path = plan / "status.json"
            status_data = _read_json(status_path) or {}
            tasks = status_data.get("tasks", {})
            if isinstance(tasks, dict):
                for task_id, entry in tasks.items():
                    status = entry if isinstance(entry, str) else (entry.get("status") if isinstance(entry, dict) else None)
                    all_tasks[f"{plan.name}:{task_id}"] = (plan.name, status) if isinstance(status, str) else (plan.name, "")
            moved = False
            try:
                moved = status_path.stat().st_mtime >= cutoff
            except OSError:
                pass
            for job in (snodo / "jobs").iterdir() if (snodo / "jobs").is_dir() else ():
                state = _read_json(job / "state.json") if job.is_dir() else None
                task = _read_json(job / "task.json") if job.is_dir() else None
                if not state or not task:
                    continue
                task_id = task.get("task_id") or task.get("retry_task_id")
                ref = str(task_id or "")
                stamps = [_timestamp(state.get(key)) for key in ("created_at", "started_at", "completed_at")]
                if any(value is not None and value >= cutoff for value in stamps):
                    owner = str(task.get("plan_name") or "")
                    if owner == plan.name or ref.startswith(plan.name + ":"):
                        moved = True
                    if not owner and ref and ref not in all_tasks:
                        status = state.get("status")
                        if isinstance(status, str):
                            all_tasks[ref] = ("", status)
            if moved:
                recent_plans.add(plan.name)
        recent_tasks = {ref: status for ref, (plan, status) in all_tasks.items()
                        if plan in recent_plans or (not plan and _task_recent(ref, snodo, cutoff))}
        task_statuses.update(status for status in recent_tasks.values() if status)
        try:
            repo = open_repo(str(root))
            commits = sum(1 for commit in repo.iter_commits("--all") if commit.committed_date >= cutoff)
        except Exception:
            commits = UNAVAILABLE.copy()
        moved = bool(recent_plans or recent_tasks or (isinstance(commits, int) and commits))
        return {
            "active": moved,
            "plans_queues": {"plans": len(recent_plans), "queues": len({queue_map.get(name) for name in recent_plans if queue_map.get(name)})},
            "waves": sum(len(_plan_waves(plan)) for plan in plan_dirs if plan.name in recent_plans),
            "tasks": {"total": len(recent_tasks), "by_status": dict(task_statuses)},
            "recons": UNAVAILABLE.copy(),
            "commits": commits,
        }
    except (OSError, PermissionError):
        return {key: UNAVAILABLE.copy() for key in ("active", "plans_queues", "waves", "tasks", "recons", "commits")}


def _task_recent(ref: str, snodo: Path, cutoff: float) -> bool:
    jobs = snodo / "jobs"
    try:
        for job in jobs.iterdir():
            task = _read_json(job / "task.json")
            state = _read_json(job / "state.json")
            if task and state and str(task.get("task_id") or task.get("retry_task_id") or "") == ref:
                return any((stamp := _timestamp(state.get(key))) is not None and stamp >= cutoff
                           for key in ("created_at", "started_at", "completed_at"))
    except OSError:
        return False
    return False


def _plan_waves(plan: Path) -> list[Any]:
    try:
        import yaml
        data = yaml.safe_load((plan / "plan.yml").read_text(encoding="utf-8")) or {}
        waves = data.get("waves", [])
        return waves if isinstance(waves, list) else []
    except (OSError, ValueError, TypeError):
        return []
