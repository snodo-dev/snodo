"""Cloud lease admission — exchange API key for a short-lived lease before sending.

FILE: snodo/infrastructure/cloud_lease.py

Agreed admission model:
Before sending anything (audit ingest or liveness snapshots), a client exchanges
its API key once for a short-lived lease consisting of:
- A URL-safe, fixed-length lease identifier.
- An opaque bearer token.

The client then sends to a path containing that identifier, presenting the token.
Near expiry it renews.

Three non-negotiable properties:
1. The identifier belongs in the path, never a header (edge filters on path shape).
2. The token is opaque: never parsed, never validated locally, never logged.
3. Refusal at exchange is terminal (HTTP 4xx except 429), recorded in CloudSyncState
   across processes, permanently halting further sends.

Unreachable exchange (HTTP 5xx, network error, 429) goes quiet with spread-out
delay (jittered backoff) to avoid stampeding recovering services.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import httpx

from snodo.infrastructure.cloud_sync import CloudSyncState

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CloudLease:
    """A short-lived cloud admission lease."""

    lease_id: str
    token: str
    expires_at: float

    def is_near_expiry(self, window_seconds: float = 30.0) -> bool:
        """True when the lease has expired or is within *window_seconds* of expiry."""
        return time.time() >= self.expires_at - window_seconds

    def __repr__(self) -> str:
        # Token is opaque and NEVER logged or shown in repr
        return f"<CloudLease id={self.lease_id!r} expires_at={self.expires_at}>"


_lock = threading.Lock()
_current_lease: Optional[CloudLease] = None
_quiet_until: float = 0.0
_consecutive_failures: int = 0


def reset_admission_state() -> None:
    """Reset in-memory lease and quiet state. Test seam."""
    global _current_lease, _quiet_until, _consecutive_failures
    with _lock:
        _current_lease = None
        _quiet_until = 0.0
        _consecutive_failures = 0


def get_current_lease() -> Optional[CloudLease]:
    """Return the cached lease if valid and not near expiry."""
    with _lock:
        if _current_lease is not None and not _current_lease.is_near_expiry():
            return _current_lease
    return None


def get_admission_lease(
    api_key: str,
    lease_url: str,
    session_id: Optional[str] = None,
    sync_state: Optional[CloudSyncState] = None,
    force: bool = False,
) -> Optional[CloudLease]:
    """Obtain or renew a lease, exchanging the API key if needed.

    Returns None if:
    - The session/API key has been permanently refused (unless force=True).
    - The exchange is in a quiet window after an unreachable attempt.
    - The exchange failed (refused or unreachable).
    """
    state = sync_state or CloudSyncState()
    sid = session_id or ""

    # 1. Check if terminal refusal is recorded across processes
    if not force and state.is_refused(sid):
        _logger.debug("Cloud admission skipped: session %s is permanently refused", sid)
        return None

    # 2. Check cached lease
    with _lock:
        if _current_lease is not None and not _current_lease.is_near_expiry():
            return _current_lease

        # 3. Check quiet window for unreachable exchange
        now = time.monotonic()
        if now < _quiet_until:
            _logger.debug(
                "Cloud admission exchange quiet until %.2f (%.1fs remaining); silence",
                _quiet_until, _quiet_until - now,
            )
            return None

    # 4. Perform exchange
    return _perform_exchange(api_key, lease_url, sid, state)


def _perform_exchange(
    api_key: str,
    lease_url: str,
    session_id: str,
    state: CloudSyncState,
) -> Optional[CloudLease]:
    global _current_lease, _quiet_until, _consecutive_failures

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"api_key": api_key}
    body = json.dumps(payload).encode("utf-8")

    try:
        response = httpx.post(lease_url, content=body, headers=headers, timeout=10.0)

        # HTTP 2xx: Success
        if 200 <= response.status_code < 300:
            try:
                data = response.json()
            except Exception:
                data = {}
            lease_id = data.get("lease_id") or data.get("id") or data.get("identifier")
            token = data.get("token") or data.get("bearer_token") or data.get("access_token")

            if not lease_id or not token:
                _handle_unreachable(None, status_code=response.status_code)
                return None

            expires_at = _parse_expiry(data)
            lease = CloudLease(lease_id=str(lease_id), token=str(token), expires_at=expires_at)

            with _lock:
                _current_lease = lease
                _consecutive_failures = 0
                _quiet_until = 0.0

            if session_id:
                state.clear_refusal(session_id)
            return lease

        # HTTP 4xx (except 429): Terminal refusal
        if 400 <= response.status_code < 500 and response.status_code != 429:
            reason = f"HTTP {response.status_code}: {response.text[:500].strip() or 'Exchange refused'}"
            _logger.warning("Cloud admission refusal HTTP %d on session=%s: %s", response.status_code, session_id, reason)
            if session_id:
                state.record_refusal(session_id, reason=reason, status_code=response.status_code)
            state.record_refusal("", reason=reason, status_code=response.status_code)
            with _lock:
                _current_lease = None
                _quiet_until = 0.0
            return None

        # HTTP 429 or 5xx: Unreachable
        _handle_unreachable(response)
        return None

    except Exception as exc:
        # Network errors / timeouts
        _handle_unreachable(None, exc=exc)
        return None


def _parse_expiry(data: dict[str, Any]) -> float:
    """Parse expiry from response metadata (never from token)."""
    if "expires_at" in data:
        val = data["expires_at"]
        if isinstance(val, (int, float)):
            return float(val)
        try:
            return datetime.fromisoformat(str(val)).timestamp()
        except (ValueError, TypeError) as exc:
            _logger.debug("Failed to parse expires_at %r: %s", val, exc)
    if "expires_in" in data:
        try:
            return time.time() + float(data["expires_in"])
        except (ValueError, TypeError) as exc:
            _logger.debug("Failed to parse expires_in: %s", exc)
    if "ttl" in data:
        try:
            return time.time() + float(data["ttl"])
        except (ValueError, TypeError) as exc:
            _logger.debug("Failed to parse ttl: %s", exc)
    # Default: short-lived 5-minute lease (300 seconds)
    return time.time() + 300.0


def _handle_unreachable(
    response: Optional[httpx.Response],
    exc: Optional[Exception] = None,
    status_code: Optional[int] = None,
) -> None:
    """Enter quiet window with jittered backoff to avoid stampeding recovering services."""
    global _quiet_until, _consecutive_failures

    code = response.status_code if response is not None else status_code

    with _lock:
        _consecutive_failures += 1
        failures = _consecutive_failures

    # If 429 with Retry-After, respect it
    wait: float = 0.0
    if response is not None and response.status_code == 429:
        retry_hdr = response.headers.get("Retry-After")
        if retry_hdr:
            try:
                wait = float(retry_hdr)
            except ValueError:
                wait = 5.0
    if wait <= 0:
        base = min(60.0, 5.0 * (2 ** min(failures - 1, 4)))
        # Spread out: positive jitter
        jitter = random.uniform(1.0, 5.0)  # noqa: S311 — jitter does not require cryptographic randomness
        wait = base + jitter

    with _lock:
        _quiet_until = time.monotonic() + wait

    _logger.debug(
        "Cloud admission unreachable (%s); silent backoff for %.1fs (attempt %d)",
        exc or f"HTTP {code}", wait, failures,
    )
