"""Plain operator-facing progress for cloud audit sync."""

import sys
import logging
from urllib.parse import urlsplit

from snodo.infrastructure.cloud_backoff import cloud_backoff_seconds, retry_after_seconds

_logger = logging.getLogger(__name__)

def ingest_failure(url: str, status: int, detail: str) -> str:
    """Describe an ingest failure without exposing its lease-id path."""
    host = urlsplit(url).netloc or "cloud ingest"
    return f"{host} -> HTTP {status}: {detail.strip() or 'No server message'}"


def batch_progress(session_id: str, accepted: int, pending: int) -> None:
    """Print cumulative per-session delivery progress."""
    print(
        f"Cloud sync {session_id}: {accepted} events accepted; {pending} pending.",
        file=sys.stderr,
    )


def rate_limit_wait(session_id: str, seconds: float, retry: int, bound: int) -> None:
    """Report the wait before retrying the same batch."""
    print(
        f"Cloud sync {session_id}: rate limited; waiting {seconds:g} seconds "
        f"before retry {retry} of {bound} for the same batch.",
        file=sys.stderr,
    )


def retry_rate_limited(
    session_id: str, body: str, retry_after: float | None, attempt: int, bound: int,
) -> bool:
    """Wait before another rate-limited attempt; return false when exhausted."""
    wait = cloud_backoff_seconds(attempt + 1, retry_after)
    if attempt == bound:
        _logger.warning("Cloud sync HTTP 429 retries exhausted (session=%s): %s", session_id, body)
        return False
    _logger.warning(
        "Cloud sync HTTP 429 retry_after=%s (session=%s): %s",
        retry_after, session_id, body,
    )
    rate_limit_wait(session_id, wait, attempt + 1, bound)
    import time
    time.sleep(wait)
    return True


def response_retry_after(response: object) -> float | None:
    """Read the server's retry delay from its header or JSON body."""
    headers = getattr(response, "headers", None)
    delay = retry_after_seconds(headers)
    if delay is not None:
        return delay
    try:
        value = response.json().get("retry_after")
    except (AttributeError, TypeError, ValueError):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, float(value))
    if isinstance(value, str):
        return retry_after_seconds({"Retry-After": value})
    return None


def session_summary(session_id: str, accepted: int, pending: int, rate_limited: bool) -> None:
    """Print one final summary for a session with work to send."""
    suffix = "; stopped early because of rate limiting" if rate_limited else ""
    print(
        f"Cloud sync {session_id} summary: {accepted} accepted, {pending} still pending{suffix}.",
        file=sys.stderr,
    )
