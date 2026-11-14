"""Read-only gate retry records for web dashboards.

The existence of a ``<task>_fix_<n>`` task record identifies a send-back.
Attempt timestamps and stopping validator results come from the original
``.snodo/tasks/<task>/state.json`` and its archived ``attempts/*.json``;
validator IDs are taken from ``halt.validator_results``. The plan and current
task status come from ``DashboardDataProvider.get_tasks()``, which reads
``.snodo/plans/<plan>/status.json``. Missing recorded values remain ``None``.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from snodo.dashboard.providers import DashboardDataProvider

_FIX_TASK = re.compile(r"^(?P<task>.+)_fix_(?P<number>\d+)$")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        except ValueError:
            return None
    return None


def _attempts(task_dir: Path) -> list[dict[str, Any]]:
    records = [_read_json(task_dir / "state.json")]
    attempts_dir = task_dir / "attempts"
    if attempts_dir.is_dir():
        records.extend(_read_json(path) for path in sorted(attempts_dir.glob("*.json")))
    return records


def _stopping_validator(state: dict[str, Any]) -> str | None:
    halt = state.get("halt")
    if not isinstance(halt, dict):
        return None
    results = halt.get("validator_results")
    if not isinstance(results, list):
        return None
    for result in results:
        if isinstance(result, dict) and result.get("severity") == "blocker":
            validator_id = result.get("validator_id")
            if isinstance(validator_id, str) and validator_id:
                return validator_id
    return None


def gate_records(project_path: str | Path, window_days: int, now: datetime | None = None) -> list[dict[str, Any]]:
    """Return one row for each plan task with a send-back in the time window."""
    root = Path(project_path)
    snodo = root / ".snodo"
    if not snodo.is_dir():
        return []

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = current.timestamp() - max(0, window_days) * 86400
    status_by_task: dict[tuple[str, str], str] = {}
    for task in DashboardDataProvider(str(root)).get_tasks(""):
        status_by_task[(task["plan_name"], task["task_id"])] = task["status"]

    tasks_dir = snodo / "tasks"
    if not tasks_dir.is_dir():
        return []
    grouped: dict[tuple[str | None, str], list[tuple[datetime, str | None]]] = {}
    try:
        fix_dirs = sorted(path for path in tasks_dir.iterdir() if path.is_dir() and _FIX_TASK.match(path.name))
    except OSError:
        return []
    for fix_dir in fix_dirs:
        match = _FIX_TASK.match(fix_dir.name)
        assert match is not None
        task_id = match.group("task")
        original_dir = tasks_dir / task_id
        if not original_dir.is_dir():
            continue
        for state in _attempts(original_dir):
            halt = state.get("halt")
            if not isinstance(halt, dict) or halt.get("status") != "blocked":
                continue
            # Archived attempt files capture the completed blocked state.
            stamp = _timestamp(state.get("completed_at"))
            if stamp is None or stamp.timestamp() < cutoff or stamp > current:
                continue
            plan = state.get("plan_name")
            if not isinstance(plan, str) or not plan:
                plan = None
            key = (plan, task_id)
            grouped.setdefault(key, []).append((stamp, _stopping_validator(state)))

    records = []
    for (plan, task), sends in grouped.items():
        stamp, validator = max(sends, key=lambda item: item[0])
        status = status_by_task.get((plan or "", task))
        records.append({
            "task": task,
            "plan": plan,
            "validator": validator,
            "outcome": "landed_after_retry" if status == "completed" else "not_landed",
            "when": stamp.isoformat(),
        })
    return sorted(records, key=lambda record: (record["when"], record["plan"] or "", record["task"]))


def summarize_gates(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate gate rows by validator; missing IDs display as ``unknown``."""
    counts: dict[str, int] = {}
    for record in records:
        validator = record.get("validator") or "unknown"
        counts[validator] = counts.get(validator, 0) + 1
    return {
        "sent_back": len(records),
        "landed_after_retry": sum(record.get("outcome") == "landed_after_retry" for record in records),
        "by_validator": [
            {"validator": validator, "count": count}
            for validator, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ],
    }
