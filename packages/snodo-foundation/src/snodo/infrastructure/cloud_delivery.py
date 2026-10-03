"""Run-level policy for outbound cloud delivery."""

import os
from typing import Any, Optional


def cloud_delivery_skip_reason(audit_log: Any = None) -> Optional[str]:
    """Return why this run's outbound cloud delivery should be suppressed.

    Audit history remains local and complete. The coder is read from recorded
    task usage, avoiding a new run-state field or configuration switch.
    """
    if os.environ.get("SNODO_BENCHMARK") == "1":
        return "benchmark run"
    for event in getattr(audit_log, "events", ()):
        data = getattr(event, "data", None)
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
