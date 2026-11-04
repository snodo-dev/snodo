"""Pure roll-up and text formatting for persisted per-call usage.

``output_tokens_per_second`` on an input record is an optional, explicitly
provider-reported generation rate. Existing usage producers do not supply it.
The same key on a summary row is either that rate (``reported``) or the
calculated output-token rate (``calculated``); it is never persisted here.
"""

from collections import defaultdict
from math import isfinite
from typing import Any


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _tokens(record: dict, primary: str, fallback: str) -> int | float | None:
    """Prefer the canonical count; null counts may fall back to the alias."""
    value = record.get(primary)
    if value is None:
        value = record.get(fallback)
    return value if _number(value) else None


def _row(role: str, records: list[dict]) -> dict:
    inputs = [_tokens(record, "input_tokens", "prompt_tokens") for record in records]
    outputs = [_tokens(record, "output_tokens", "completion_tokens") for record in records]
    durations = [record.get("duration_ms") for record in records]
    input_tokens = sum(inputs) if records and all(v is not None for v in inputs) else None
    output_tokens = sum(outputs) if records and all(v is not None for v in outputs) else None
    complete_time = bool(records) and all(_number(v) and v >= 0 for v in durations)
    seconds = sum(durations) / 1000 if complete_time else None
    rate = None
    source = None
    if seconds is not None and seconds > 0:
        reported = [record.get("output_tokens_per_second") for record in records]
        if all(_number(v) and v >= 0 for v in reported):
            # Weight per-call provider rates by call time, never sum rates.
            rate = sum(r * d for r, d in zip(reported, durations)) / (seconds * 1000)
            source = "reported"
        elif output_tokens is not None:
            rate = output_tokens / seconds
            source = "calculated"
    return {
        "role": role,
        "calls": len(records),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "time_seconds": seconds,
        "output_tokens_per_second": rate,
        "rate_source": source,
    }


def summarize_usage(records: list[dict], total_elapsed_seconds: float | None = None) -> dict:
    """Return role/agent rows and a total, preserving unknown token counts.

    A token total is unknown if *any* call in that row lacks that count.
    ``time_seconds`` is unknown unless every call has a nonnegative duration;
    a rate additionally requires positive model time. The optional elapsed value is wall-clock
    run time, not model time; it is kept separately on the summary and total.
    A reported row rate requires a nonnegative reported rate on every call and
    complete, positive model time. Mixed provenance uses the calculated rate
    when output tokens are known. The total follows the same rule over all
    calls, not an average of role rates.
    """
    grouped: dict[str, list[dict]] = defaultdict(list)
    valid = [record for record in records if isinstance(record, dict)]
    for record in valid:
        role = record.get("role") or record.get("agent") or "unknown"
        grouped[str(role)].append(record)
    rows = [_row(role, calls) for role, calls in grouped.items()]
    elapsed = total_elapsed_seconds if _number(total_elapsed_seconds) and total_elapsed_seconds >= 0 else None
    total = _row("total", valid)
    total["total_elapsed_seconds"] = elapsed
    return {"rows": rows, "total": total, "total_elapsed_seconds": elapsed}


def _display(value: int | float | None) -> str:
    return "n/a" if value is None else str(value)


def _line(row: dict) -> str:
    time = row["time_seconds"]
    rate = row["output_tokens_per_second"]
    mark = {"calculated": "C", "reported": "R"}.get(row["rate_source"], "")
    rate_text = f"{rate:.1f} {mark}" if rate is not None else "n/a"
    time_text = f"{time:.1f}s" if time is not None else "n/a"
    return (f"{row['role']}: {_display(row['input_tokens'])} | "
            f"{_display(row['output_tokens'])} | "
            f"{time_text} | {rate_text} ({row['calls']} calls)")


def render_usage_summary(summary: dict) -> list[str]:
    """Render header, role rows, and total; unknowns appear as ``n/a``.

    Every time column displays time spent in model calls. When run elapsed
    time is available, it is shown separately on the total line.
    """
    lines = ["Usage: input | output | model time | output tokens/s"]
    lines.extend(_line(row) for row in summary["rows"])
    total = summary["total"]
    elapsed = summary.get("total_elapsed_seconds")
    lines.append(_line(total))
    if elapsed is not None:
        lines[-1] += f"; elapsed {elapsed:.1f}s"
    return lines
