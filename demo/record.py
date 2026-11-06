"""Create versioned, demo-local records from Snodo task artifacts.

The task state file is intentionally read for this hermetic demo helper, not
claimed as Snodo's stable machine interface (see ADR 022).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_RECORD_SCHEMA = "snodo.demo-run-record.v1"
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_ERROR_HALTS = {"validator_error", "internal_error", "environment_error"}


def _number(value: Any) -> int | float | None:
    return (
        value
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


def build_record(
    project_root: str | Path, task_id: str, model: str, arm: str
) -> dict[str, Any]:
    """Read one task state and return a single normalized demo record."""
    if arm not in {"governed", "bare"}:
        raise ValueError("arm must be 'governed' or 'bare'")
    empty: dict[str, Any] = {
        "schema": _RECORD_SCHEMA,
        "arm": arm,
        "model": model,
        "task_id": task_id,
        "outcome": "unknown",
        "halt_type": None,
        "wall_seconds": None,
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
        "cost_source": "unavailable",
        "worktree_path": None,
        "validator_findings": [],
    }
    if not _TASK_ID.fullmatch(task_id):
        return empty
    try:
        state = json.loads(
            (
                Path(project_root) / ".snodo" / "tasks" / task_id / "state.json"
            ).read_text()
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return empty
    halt = state.get("halt") or {}
    halt_type = halt.get("halt_type") or state.get("halt_type")
    status = state.get("status")
    if halt_type == "blocker":
        outcome = "blocked"
    elif halt_type == "escalate":
        outcome = "halted"
    elif halt_type in _ERROR_HALTS:
        outcome = "error"
    elif status == "completed" and not halt_type:
        outcome = "passed"
    else:
        outcome = "unknown"
    findings = []
    results = halt.get("validator_results") or state.get("validator_results") or []
    for result in results:
        if isinstance(result, dict):
            findings.append(
                {
                    "validator_id": result.get("validator_id"),
                    "severity": result.get("severity"),
                    "justification": str(result.get("justification") or "")[:1000],
                }
            )
    usage = state.get("usage") or []
    if isinstance(usage, dict):
        usage = [usage]
    totals: dict[str, int | float] = {}
    token_known = {"input_tokens": False, "output_tokens": False}
    cost: int | float | None = None
    cost_source = "unavailable"
    for call in usage:
        if not isinstance(call, dict):
            continue
        for key in token_known:
            value = _number(call.get(key))
            if value is not None:
                totals[key] = totals.get(key, 0) + value
                token_known[key] = True
        value = _number(call.get("cost_usd", call.get("cost")))
        if value is not None:
            cost = (cost or 0) + value
            cost_source = call.get("cost_source") or "recorded_usage"
    record = empty | {
        "outcome": outcome,
        "halt_type": halt_type,
        "wall_seconds": _number(
            state.get("wall_seconds", state.get("duration_seconds"))
        ),
        "input_tokens": totals.get("input_tokens")
        if token_known["input_tokens"]
        else None,
        "output_tokens": totals.get("output_tokens")
        if token_known["output_tokens"]
        else None,
        "cost_usd": cost,
        "cost_source": cost_source,
        "worktree_path": state.get("worktree_path"),
        "validator_findings": findings,
    }
    return record


def find_new_task_id(before: list[str] | set[str], after: list[str] | set[str]) -> str:
    """Return the sole newly listed task id, raising clearly for zero/many."""
    new_ids = sorted(set(after) - set(before))
    if len(new_ids) != 1:
        raise ValueError(
            f"expected exactly one new task id; found {len(new_ids)}: {new_ids}"
        )
    return new_ids[0]


def write_record(record: dict[str, Any], output_path: str | Path) -> Path:
    """Write one JSON record to the explicitly selected output file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return path
