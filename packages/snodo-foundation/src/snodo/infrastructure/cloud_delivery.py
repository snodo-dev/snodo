"""Run-level policy for outbound cloud delivery."""

import os
from typing import Any, Optional


def cloud_delivery_skip_reason(
    audit_log: Any = None, *, session_id: Optional[str] = None,
) -> Optional[str]:
    """Return why this run's outbound cloud delivery should be suppressed.

    Audit history remains local and complete. The coder is read from recorded
    task usage, avoiding a new run-state field or configuration switch.
    """
    if os.environ.get("SNODO_BENCHMARK") == "1":
        return "benchmark run"
    events = list(getattr(audit_log, "events", ()))
    if session_id:
        # Session-start events mark the boundary in the shared project log.
        # Inspect only the current session's suffix, never earlier sessions.
        starts = [
            index for index, item in enumerate(events)
            if getattr(item, "event_type", "") == "session_started"
            and isinstance(getattr(item, "data", None), dict)
            and item.data.get("session_id") == session_id
        ]
        if starts:
            events = events[starts[-1]:]
        else:
            # Without a boundary, usage must identify the requested session
            # explicitly; unrelated historical events are not evidence.
            events = [
                item for item in events
                if isinstance(getattr(item, "data", None), dict)
                and item.data.get("session_id") == session_id
            ]
    for item in events:
        data = getattr(item, "data", None)
        if not isinstance(data, dict):
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
