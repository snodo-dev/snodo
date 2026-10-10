"""Read-only summaries of persisted task and plan-run records."""

from __future__ import annotations

import json
import statistics
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TaskOutcome:
    task_id: str
    outcome: str | None
    halt_type: str | None
    attempts: int
    duration_seconds: float | None
    tokens: int | None
    cost_usd: float | None
    cost_source: str | None
    delivered: bool
    first_pass: bool | None


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _records_cost(state: dict[str, Any]) -> tuple[float | None, str | None]:
    cost = state.get("cost")
    summary = cost if isinstance(cost, dict) else {}
    amount = summary.get("cost_usd", summary.get("usd"))
    source = summary.get("provenance") or state.get("cost_source")
    measured = [u for u in state.get("usage", []) if isinstance(u, dict) and u.get("cost") is not None]
    if amount is None and measured:
        amount = sum(float(u["cost"]) for u in measured)
        sources = {str(u.get("cost_source") or u.get("source") or "unknown") for u in measured}
        source = ",".join(sorted(sources))
    if amount is None:
        return None, str(source) if source else None
    if not source:
        source = "provider_reported" if any(u.get("cost_source") == "provider_reported" for u in measured) else "estimated"
    return float(amount), str(source)


def summarize_task(task_id: str, state: dict[str, Any]) -> TaskOutcome:
    """Extract metrics from one persisted job or task state record.

    First-pass means the first recorded coder attempt has outcome ``resolved``
    (case-insensitive); missing or incomplete attempt history is unknown.
    """
    halt = state.get("halt") if isinstance(state.get("halt"), dict) else {}
    attempts_data = halt.get("attempts") if isinstance(halt.get("attempts"), dict) else {}
    history = attempts_data.get("history")
    history = history if isinstance(history, list) else []
    coder_attempts = [item for item in history if isinstance(item, dict)]
    first_pass = None
    if coder_attempts:
        first_pass = str(coder_attempts[0].get("outcome", "")).lower() == "resolved"
    elif attempts_data.get("coder_dispatches") == 0:
        first_pass = None
    cost, cost_source = _records_cost(state)
    summary = state.get("cost") if isinstance(state.get("cost"), dict) else {}
    usage_tokens = sum(int(u.get("total_tokens", u.get("tokens", 0)) or 0)
                       for u in state.get("usage", []) if isinstance(u, dict))
    tokens = summary.get("tokens")
    if isinstance(tokens, dict):
        tokens = tokens.get("total_tokens")
    if tokens is None and state.get("usage"):
        tokens = usage_tokens
    return TaskOutcome(
        task_id=task_id,
        outcome=halt.get("final_decision") or state.get("status"),
        halt_type=halt.get("halt_type"),
        attempts=int(attempts_data.get("total", len(history)) or 0),
        duration_seconds=summary.get("duration_seconds", state.get("duration_seconds")),
        tokens=int(tokens) if tokens is not None else None,
        cost_usd=cost,
        cost_source=cost_source,
        delivered=bool(state.get("delivered_branch")),
        first_pass=first_pass,
    )


def summarize_plan_run(plan_dir: Path | str) -> dict[str, Any]:
    """Summarize task states beneath a plan-run directory, without mutation."""
    root = Path(plan_dir)
    children = sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []
    tasks = []
    for child in children:
        state = _read(child / "state.json")
        if state:
            tasks.append(summarize_task(child.name, state))
    return _aggregate(tasks)


def _aggregate(tasks: list[TaskOutcome]) -> dict[str, Any]:
    cost_sources: dict[str, float] = {}
    for task in tasks:
        if task.cost_usd is not None:
            source = task.cost_source or "unknown"
            cost_sources[source] = cost_sources.get(source, 0.0) + task.cost_usd
    return {
        "tasks": [asdict(task) for task in tasks],
        "task_count": len(tasks),
        "outcomes": dict(Counter(task.outcome or "unknown" for task in tasks)),
        "halt_types": dict(Counter(task.halt_type or "none" for task in tasks)),
        "totals": {
            "attempts": sum(t.attempts for t in tasks),
            "tokens": sum(t.tokens or 0 for t in tasks),
            "duration_seconds": sum(t.duration_seconds or 0 for t in tasks),
            "cost_usd": sum(t.cost_usd or 0 for t in tasks if t.cost_usd is not None),
            "cost_by_source_usd": cost_sources,
            "unknown_cost_runs": sum(t.cost_usd is None for t in tasks),
            "delivered": sum(t.delivered for t in tasks),
        },
    }


def summarize_window(jobs_dir: Path | str, start: datetime, end: datetime) -> dict[str, Any]:
    """Summarize jobs whose started_at (or created_at) is in [start, end)."""
    root = Path(jobs_dir)
    tasks = []
    for child in sorted(root.iterdir()) if root.exists() else []:
        if not child.is_dir():
            continue
        state = _read(child / "state.json")
        stamp = state.get("started_at") or state.get("created_at")
        try:
            when = datetime.fromtimestamp(stamp, tz=start.tzinfo) if isinstance(stamp, (int, float)) else datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None and start.tzinfo is not None:
            when = when.replace(tzinfo=start.tzinfo)
        if start <= when < end:
            tasks.append(summarize_task(child.name, state))
    result = _aggregate(tasks)
    known_costs = [t.cost_usd for t in tasks if t.cost_usd is not None]
    for metric in ("tokens", "duration_seconds"):
        values = [getattr(t, metric) for t in tasks if getattr(t, metric) is not None]
        result["totals"][f"median_{metric}"] = statistics.median(values) if values else None
    result["totals"]["median_cost_usd"] = statistics.median(known_costs) if known_costs else None
    result["first_pass_rate"] = (
        sum(t.first_pass is True for t in tasks) / sum(t.first_pass is not None for t in tasks)
        if any(t.first_pass is not None for t in tasks) else None
    )
    return result
