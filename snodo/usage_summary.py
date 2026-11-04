"""Normalize model usage records into consistent, display-ready summaries.

Provider-reported generation speed, when available, is read from
``output_tokens_per_second``. No rate is inferred from wall-clock elapsed time;
calculated rates use the sum of per-call ``duration_ms`` values instead.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping
from typing import Any


def _number(record: Mapping[str, Any], primary: str, fallback: str) -> int | float | None:
    value = record.get(primary)
    if value is None:
        value = record.get(fallback)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def summarize_usage(
    records: Iterable[Mapping[str, Any]], total_elapsed_seconds: float | None = None
) -> dict[str, Any]:
    """Return per-role rows and a total row for usage records.

    ``total_elapsed_seconds`` is retained as summary metadata for callers that
    compare model-call time with end-to-end run time; rates use call durations.
    Missing token data remains ``None``, never zero.
    """
    grouped: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for record in records:
        role_value = record.get("role", record.get("agent", "unknown"))
        role = str(role_value) if role_value is not None else "unknown"
        row = grouped.setdefault(role, {
            "role": role, "calls": 0, "input_tokens": 0, "output_tokens": 0,
            "_input_known": False, "_output_known": False, "time_seconds": 0.0,
            "_time_known": False, "_reported_rates": [],
        })
        row["calls"] += 1
        input_tokens = _number(record, "input_tokens", "prompt_tokens")
        output_tokens = _number(record, "output_tokens", "completion_tokens")
        if input_tokens is not None:
            row["input_tokens"] += input_tokens
            row["_input_known"] = True
        if output_tokens is not None:
            row["output_tokens"] += output_tokens
            row["_output_known"] = True
        duration_ms = record.get("duration_ms")
        if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool) and duration_ms > 0:
            row["time_seconds"] += duration_ms / 1000
            row["_time_known"] = True
        reported_rate = record.get("output_tokens_per_second")
        if isinstance(reported_rate, (int, float)) and not isinstance(reported_rate, bool) and reported_rate > 0:
            row["_reported_rates"].append((reported_rate, output_tokens))

    rows = list(grouped.values())
    total = {
        "role": "total", "calls": sum(row["calls"] for row in rows),
        "input_tokens": sum(row["input_tokens"] for row in rows if row["_input_known"]),
        "output_tokens": sum(row["output_tokens"] for row in rows if row["_output_known"]),
        "_input_known": any(row["_input_known"] for row in rows),
        "_output_known": any(row["_output_known"] for row in rows),
        "time_seconds": sum(row["time_seconds"] for row in rows),
        "_time_known": any(row["_time_known"] for row in rows),
        "_reported_rates": [rate for row in rows for rate in row["_reported_rates"]],
    }
    for row in [*rows, total]:
        reported = row["_reported_rates"]
        if reported:
            weighted = [(rate, tokens) for rate, tokens in reported if tokens is not None and tokens > 0]
            row["output_tokens_per_second"] = (
                sum(rate * tokens for rate, tokens in weighted) / sum(tokens for _, tokens in weighted)
                if weighted else sum(rate for rate, _ in reported) / len(reported)
            )
            row["rate_source"] = "reported"
        elif row["_time_known"] and row["time_seconds"] > 0 and row["_output_known"]:
            row["output_tokens_per_second"] = row["output_tokens"] / row["time_seconds"]
            row["rate_source"] = "calculated"
        else:
            row["output_tokens_per_second"] = None
            row["rate_source"] = None
        row["input_tokens"] = row["input_tokens"] if row["_input_known"] else None
        row["output_tokens"] = row["output_tokens"] if row["_output_known"] else None
        for internal in ("_input_known", "_output_known", "_time_known", "_reported_rates"):
            del row[internal]
    return {"rows": rows, "total": total, "total_elapsed_seconds": total_elapsed_seconds}


def render_usage_summary(summary: Mapping[str, Any]) -> list[str]:
    """Render a summary as a header, role rows, and a total line."""
    lines = ["Usage: role | input tokens | output tokens | model time | output tok/s"]
    rows = [*summary.get("rows", []), summary.get("total", {})]
    for row in rows:
        input_tokens = row.get("input_tokens")
        output_tokens = row.get("output_tokens")
        seconds = row.get("time_seconds")
        rate = row.get("output_tokens_per_second")
        source = row.get("rate_source")
        rate_text = "n/a" if rate is None else f"{rate:.1f} {('R' if source == 'reported' else 'C')}"
        time_text = "n/a" if seconds is None or seconds <= 0 else f"{seconds:.1f}s"
        lines.append(
            f"{row.get('role', 'total')}: {input_tokens if input_tokens is not None else 'n/a'} | "
            f"{output_tokens if output_tokens is not None else 'n/a'} | {time_text} | {rate_text}"
        )
    return lines
