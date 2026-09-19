"""Shared backoff policy for cloud senders."""

import secrets
from email.utils import parsedate_to_datetime
from datetime import UTC, datetime
from typing import Any


MAX_CLOUD_BACKOFF_SECONDS = 60.0


def retry_after_seconds(headers: Any) -> float | None:
    """Parse a valid Retry-After value, returning seconds or None."""
    value = headers.get("Retry-After") if headers is not None else None
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            date = parsedate_to_datetime(str(value))
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            return max(0.0, date.timestamp() - datetime.now(UTC).timestamp())
        except (TypeError, ValueError, OverflowError):
            return None


def cloud_backoff_seconds(
    failures: int,
    server_delay: float | None = None,
    *,
    random_value: float | None = None,
    cap: float = MAX_CLOUD_BACKOFF_SECONDS,
) -> float:
    """Return one bounded delay shared by liveness and ingest.

    A server delay is authoritative and is capped so a running plan keeps its
    visibility floor. Locally generated delays grow exponentially and receive
    positive jitter; jitter only makes them later, never earlier.
    """
    cap = max(0.0, float(cap))
    if server_delay is not None:
        return min(max(0.0, server_delay), cap)
    failures = max(1, int(failures))
    base = min(cap, float(2 ** min(failures - 1, 30)))
    if random_value is None:
        random_value = secrets.SystemRandom().random()
    jitter = min(1.0, max(0.0, float(random_value))) * base * 0.25
    return min(cap, base + jitter)


def is_transient_status(status_code: int) -> bool:
    """Return whether a response represents a cloud condition worth backing off."""
    return status_code == 429 or status_code >= 500
