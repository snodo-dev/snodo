"""Cloud-bound projection of persisted provider usage records."""

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class UsageRecord(BaseModel):
    """Pinned provider-usage projection for cloud interface v7."""

    model_config = ConfigDict(extra="allow")
    role: Literal["coder", "validator", "recon"]
    task_ref: str | None = None
    attempt: int | None = None
    validator_id: str | None = None
    phase: Literal["pre", "post"] | None = None
    agent: str | None = None
    coder: str | None = None
    model: str
    served_model: str | None = None
    provider: str | None = None
    calls: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cost_usd: float | None = None
    duration_ms: float | None = None
    outcome: Literal["succeeded", "failed", "timed_out"]
    error_class: str | None = None


def _aggregate_usage(records: list[dict], role: str, **identity: Any) -> dict | None:
    """Project per-call records while preserving unknowns and provider costs."""
    if not records:
        return None
    out: dict[str, Any] = {"role": role, **identity}
    for key in ("model", "served_model", "provider"):
        out[key] = next((r.get(key) for r in records if r.get(key)), "" if key == "model" else None)
    out["calls"] = len(records)
    for target, aliases in {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "cache_read_tokens": ("cache_read_tokens",),
        "cache_write_tokens": ("cache_write_tokens",),
    }.items():
        values = [next((r.get(key) for key in aliases if key in r), None) for r in records]
        out[target] = sum(values) if all(isinstance(v, (int, float)) for v in values) else None
    costs = [r.get("cost") if r.get("cost_source") == "provider" else None for r in records]
    out["cost_usd"] = sum(costs) if all(isinstance(v, (int, float)) for v in costs) else None
    durations = [r.get("duration_ms") for r in records]
    out["duration_ms"] = sum(durations) if all(isinstance(v, (int, float)) for v in durations) else None
    outcomes = {r.get("outcome") for r in records}
    out["outcome"] = "timed_out" if "timed_out" in outcomes else "succeeded" if outcomes <= {"success", "succeeded"} else "failed"
    out["error_class"] = next((r.get("error_class") for r in records if r.get("error_class")), None)
    return out


def task_usage_records(project_root: str, task_ref: str) -> list[dict]:
    """Build coder usage records from the task's persisted usage list."""
    try:
        state = json.loads((Path(project_root) / ".snodo" / "tasks" / task_ref / "state.json").read_text())
    except (OSError, ValueError):
        return []
    usage = state.get("usage", [])
    if not isinstance(usage, list):
        return []
    calls = [r for r in usage if isinstance(r, dict) and r.get("role") == "coder"]
    if not calls:
        return []
    match = re.search(r"_fix_(\d+)$", task_ref)
    row = _aggregate_usage(
        calls, "coder", task_ref=task_ref,
        attempt=int(match.group(1)) if match else 0,
        coder=str(calls[0].get("coder") or "litellm"),
    )
    return [row] if row else []
