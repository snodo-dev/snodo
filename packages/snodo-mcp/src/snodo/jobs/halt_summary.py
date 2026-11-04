"""Format a bounded, plain-text summary of halt validator findings."""

from __future__ import annotations

import re
from typing import Any

_MAX_VALIDATORS = 3
_JUSTIFICATION_LIMIT = 180
_RESULT_LIMIT = 600
_SEVERITY_ORDER = {"blocker": 0, "error": 0, "warn": 1, "pass": 2}


def _plain(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    # Remove line/control characters and neutralise common chat markup and mentions.
    value = " ".join(value.split())
    value = re.sub(r"([*_`~|<>])", r"\\\1", value)
    value = value.replace("@", "＠")
    if len(value) > limit:
        value = value[: max(0, limit - 1)].rstrip() + "…"
    return value


def format_halt_summary(payload: Any) -> str:
    """Return a short readable reason and validator list, or empty for bad input."""
    try:
        if not isinstance(payload, dict):
            return ""
        lines: list[str] = []
        reason = _plain(payload.get("reason"), _JUSTIFICATION_LIMIT)
        if reason:
            lines.append(reason)
        results = payload.get("validator_results")
        valid = []
        if isinstance(results, (list, tuple)):
            for item in results:
                if not isinstance(item, dict):
                    continue
                validator_id = _plain(item.get("id"), 80)
                severity = _plain(item.get("severity"), 24)
                justification = _plain(item.get("justification"), _JUSTIFICATION_LIMIT)
                if validator_id and severity and justification:
                    valid.append((item, validator_id, severity, justification))
        valid.sort(key=lambda row: _SEVERITY_ORDER.get(row[2].lower(), 3))
        for _, validator_id, severity, justification in valid[:_MAX_VALIDATORS]:
            lines.append(f"{validator_id} ({severity}): {justification}")
        if len(valid) > _MAX_VALIDATORS:
            lines.append(f"and {len(valid) - _MAX_VALIDATORS} more")
        result = "\n".join(lines)
        if len(result) > _RESULT_LIMIT:
            result = result[: _RESULT_LIMIT - 1].rstrip() + "…"
        return result
    except Exception:  # Malformed external payloads must never affect notification delivery.
        return ""
