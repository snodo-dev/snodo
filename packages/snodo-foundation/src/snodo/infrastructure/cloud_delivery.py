"""Run-level policy for outbound cloud delivery."""

import os
from typing import Any, Optional


def cloud_delivery_skip_reason(
    audit_log: Any = None, *, session_id: Optional[str] = None,
    task_ref: Optional[str] = None, job_id: Optional[str] = None,
) -> Optional[str]:
    """Return why this run's outbound cloud delivery should be suppressed.

    Audit history remains local and complete. The coder is read from recorded
    task usage, avoiding a new run-state field or configuration switch.
    """
    if os.environ.get("SNODO_BENCHMARK") == "1":
        return "benchmark run"
    identifiers = {
        key: value for key, value in (
            ("session_id", session_id), ("task_ref", task_ref),
            ("job_id", job_id),
        ) if value
    }
    if not identifiers:
        return None
    for item in getattr(audit_log, "events", ()):
        data = getattr(item, "data", None)
        if not isinstance(data, dict) or not any(
            data.get(key) == value for key, value in identifiers.items()
        ):
            continue
        usage = data.get("usage", ())
        if isinstance(usage, list) and any(
            isinstance(item, dict) and item.get("coder") == "mock"
            for item in usage
        ):
            return "mock coder run"
        if data.get("coder") == "mock":
            return "mock coder run"
    return None
