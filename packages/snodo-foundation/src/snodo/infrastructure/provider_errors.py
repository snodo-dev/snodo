"""Classification and retry hints for provider completion failures."""

import json
import re

MAX_PROVIDER_RETRY_DELAY_SECONDS = 30.0


def provider_retry_delay(error: Exception | str) -> float | None:
    """Return a provider-suggested delay, capped to keep retries bounded."""
    text = str(error)
    match = re.search(r'"retryDelay"\s*:\s*"?([0-9.]+)s?"?', text)
    if match:
        return min(float(match.group(1)), MAX_PROVIDER_RETRY_DELAY_SECONDS)
    retry_after = getattr(error, "retry_after", None)
    try:
        if retry_after is not None:
            return min(max(0.0, float(retry_after)), MAX_PROVIDER_RETRY_DELAY_SECONDS)
    except (TypeError, ValueError):
        pass
    return None


def is_transient_provider_error(error: Exception | str) -> bool:
    """Recognize retryable throttling, server, and transport failures."""
    status = getattr(error, "status_code", None)
    text = str(error).lower()
    if status == 429 or (isinstance(status, int) and status >= 500):
        return True
    if any(term in text for term in (
        "resource_exhausted", "rate limit", "too many requests",
        "failed to read request body", "connection reset", "connection aborted",
        "connection error", "connection dropped", "broken pipe", "timed out",
    )):
        return True
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict):
        code = payload.get("code") or payload.get("status")
        return code == 429 or (isinstance(code, int) and code >= 500)
    return False
