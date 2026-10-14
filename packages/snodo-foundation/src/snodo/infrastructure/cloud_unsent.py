"""Archive-aware selection of audit events not yet delivered to the cloud."""

from collections.abc import Iterable
from typing import Any


def events_after_cursor(audit_log: Any, cursor: int) -> Iterable[Any]:
    """Yield events after *cursor*, reading archives only when they may matter."""
    current_events = getattr(audit_log, "events", [])
    first_current = min((event.sequence for event in current_events), default=None)
    if first_current is None or cursor < first_current:
        history = getattr(type(audit_log), "iter_history", None)
        if history is not None:
            yield from (event for event in audit_log.iter_history() if event.sequence > cursor)
            return
    yield from (event for event in current_events if event.sequence > cursor)
