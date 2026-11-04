"""Collect display-only run metrics for model comparisons."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from snodo.usage_summary import summarize_usage

_SEVERITIES = ("blocker", "warn", "escalate", "pass", "validator_error", "internal_error")


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _elapsed_seconds(state: dict[str, Any]) -> float | None:
    direct = _number(state.get("elapsed_ms"))
    if direct is None:
        direct = _number(state.get("wall_time_ms"))
    if direct is not None:
        return direct / 1000
    start, end = state.get("started_at"), state.get("completed_at")
    if isinstance(start, str) and isinstance(end, str):
        try:
            return max(0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())
        except ValueError:
            return None
    return None


def _metrics(state: dict[str, Any], root: Path, job_id: str | None = None) -> dict[str, Any]:
    halt = state.get("halt") if isinstance(state.get("halt"), dict) else {}
    coder = halt.get("coder_report") if isinstance(halt.get("coder_report"), dict) else {}
    usage = state.get("usage") if isinstance(state.get("usage"), list) else []
    summary = summarize_usage([r for r in usage if isinstance(r, dict)])
    rows = {row.get("role"): row for row in summary["rows"]}
    coder_usage = rows.get("coder", {})
    validators = [r for r in usage if isinstance(r, dict) and str(r.get("role", r.get("agent", ""))).startswith("validator")]
    validator_summary = summarize_usage(validators)["total"]
    calls = coder_usage.get("calls")
    turns = _number(coder.get("turns_used"))
    results = halt.get("validator_results") or state.get("validator_results") or []
    counts = {severity: 0 for severity in _SEVERITIES}
    if isinstance(results, list):
        for result in results:
            if isinstance(result, dict):
                severity = result.get("severity")
                if severity in counts:
                    counts[severity] += 1
    cost = state.get("cost") if isinstance(state.get("cost"), dict) else {}
    cost_value = _number(state.get("total_cost"))
    if cost_value is None:
        cost_value = _number(cost.get("total"))
    if cost_value is None:
        cost_value = _number(sum(r.get("cost", 0) for r in usage if _number(r.get("cost")) is not None)) if any(_number(r.get("cost")) is not None for r in usage) else None
    return {
        "coder_time_seconds": _number(coder.get("wall_time_ms")) / 1000 if _number(coder.get("wall_time_ms")) is not None else coder_usage.get("time_seconds") if coder_usage.get("time_seconds") else None,
        "elapsed_seconds": _elapsed_seconds(state),
        "coder_turns": turns if turns is not None else calls,
        "coder_turns_source": "recorded" if turns is not None else "model_calls" if calls is not None else None,
        "coder_input_tokens": coder_usage.get("input_tokens"),
        "coder_output_tokens": coder_usage.get("output_tokens"),
        "coder_output_tokens_per_second": coder_usage.get("output_tokens_per_second"),
        "coder_rate_source": coder_usage.get("rate_source"),
        "validator_input_tokens": validator_summary.get("input_tokens"),
        "validator_output_tokens": validator_summary.get("output_tokens"),
        "cost": cost_value,
        "post_validation_findings": counts,
    }


def comparison_metrics(root: str | Path, baseline: dict[str, Any], candidate: dict[str, Any], candidate_job: str) -> dict[str, Any]:
    """Return baseline/candidate display metrics, tolerating absent job evidence."""
    root = Path(root)
    baseline_state: dict[str, Any] = {}
    copied = baseline.get("metrics")
    if isinstance(copied, dict):
        baseline_state = copied
    else:
        task_state = _read(root / ".snodo" / "tasks" / str(baseline.get("task") or "") / "state.json")
        job_id = task_state.get("job_id")
        if isinstance(job_id, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", job_id):
            baseline_state = _read(root / ".snodo" / "jobs" / job_id / "state.json")
    return {
        "baseline": _metrics(baseline_state, root),
        "candidate": _metrics(candidate, root, candidate_job),
        "token_comparability_note": "In-place and LiteLLM coders count prompt tokens differently; input tokens are not directly comparable.",
    }
