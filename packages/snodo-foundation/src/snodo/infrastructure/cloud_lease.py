"""Cloud lease admission — exchange API key for a short-lived lease before sending.

FILE: snodo/infrastructure/cloud_lease.py

Admission model:
Before sending anything (audit ingest or liveness snapshots), a client POSTs to
the app host's `/m` route with its full API key as Bearer auth. The response's
`jti`, opaque token, expiry, and cadence form the lease. Ingest is addressed by
`jti`; liveness uses the same route on the app host. The optional
`interface_version` advertises the cloud's accepted wire version; absent or
invalid metadata leaves the client on v5. Near expiry the client renews.

Three non-negotiable properties:
1. Liveness jti belongs in the path, never a header (edge filters on path shape).
2. The token is opaque: never parsed, never validated locally, never logged.
3. Refusal at exchange is terminal (HTTP 4xx except 404 and 429), recorded in CloudSyncState
   across processes, permanently halting further sends.

Unavailable exchange (network error, 429, 5xx, or 404 route mismatch) backs off
with spread-out delay (jittered backoff) to avoid stampeding recovering services;
every answered HTTP failure is still reported with its URL, status, and message.
"""

from __future__ import annotations

import logging
import random
import sys
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

    jti: str
    token: str
    expires_at: float
    cadence_s: Optional[float] = None
    interface_version: Optional[int] = None

    @property
    def lease_id(self) -> str:
        """Compatibility name for callers predating the jti wire field."""
        return self.jti

    def is_near_expiry(self, window_seconds: float = 30.0) -> bool:
        """True when the lease has expired or is within *window_seconds* of expiry."""
        return time.time() >= self.expires_at - window_seconds

    def __repr__(self) -> str:
        # Token is opaque and NEVER logged or shown in repr
        return f"<CloudLease jti={self.jti!r} expires_at={self.expires_at}>"


_lock = threading.Lock()
_current_lease: Optional[CloudLease] = None
_current_lease_session_id: Optional[str] = None
_quiet_until: float = 0.0
_consecutive_failures: int = 0
_last_admission_error: Optional[str] = None


def get_last_admission_error() -> Optional[str]:
    """Return the latest mint error, safe to show (never includes credentials)."""
    return _last_admission_error


def reset_admission_state() -> None:
    """Reset in-memory lease and quiet state. Test seam."""
    global _current_lease, _current_lease_session_id, _quiet_until, _consecutive_failures, _last_admission_error
    with _lock:
        _current_lease = None
        _current_lease_session_id = None
        _quiet_until = 0.0
        _consecutive_failures = 0
        _last_admission_error = None


def get_current_lease(session_id: Optional[str] = None) -> Optional[CloudLease]:
    """Return a valid cached lease, optionally restricted to its mint session."""
    with _lock:
        if (
            _current_lease is not None
            and (session_id is None or _current_lease_session_id == session_id)
            and not _current_lease.is_near_expiry()
        ):
            return _current_lease
    return None


def invalidate_lease(lease: CloudLease) -> None:
    """Discard *lease* when the send endpoint rejects its credential."""
    global _current_lease, _current_lease_session_id
    with _lock:
        if _current_lease is lease:
            _current_lease = None
            _current_lease_session_id = None


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
        if (
            _current_lease is not None
            and _current_lease_session_id == sid
            and not _current_lease.is_near_expiry()
        ):
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
    global _current_lease, _current_lease_session_id, _quiet_until, _consecutive_failures

    global _last_admission_error
    headers = {"Authorization": f"Bearer {api_key}"}
    mint_url = f"{lease_url.rstrip('/')}/m"

    try:
        response = httpx.post(mint_url, headers=headers, timeout=10.0)

        # HTTP 2xx: Success
        if 200 <= response.status_code < 300:
            try:
                data = response.json()
            except Exception:
                data = {}
            lease_id = data.get("jti")
            token = data.get("token")

            if not lease_id or not token:
                _last_admission_error = f"{mint_url} -> HTTP {response.status_code}: invalid mint response"
                print(f"Cloud lease mint failed: {_last_admission_error}", file=sys.stderr)
                _handle_unreachable(None, status_code=response.status_code)
                return None

            expires_at = _parse_expiry(data)
            try:
                cadence_s = float(data["cadence_s"]) if data.get("cadence_s") is not None else None
            except (TypeError, ValueError):
                cadence_s = None
            interface_version = data.get("interface_version")
            if isinstance(interface_version, bool) or not isinstance(interface_version, int):
                interface_version = None
            lease = CloudLease(
                jti=str(lease_id), token=str(token), expires_at=expires_at,
                cadence_s=cadence_s, interface_version=interface_version,
            )

            with _lock:
                _current_lease = lease
                _current_lease_session_id = session_id
                _consecutive_failures = 0
                _quiet_until = 0.0
                _last_admission_error = None

            if session_id:
                state.clear_refusal(session_id)
            return lease

        message = response.text[:500].strip() or "No server message"
        _last_admission_error = f"{mint_url} -> HTTP {response.status_code}: {message}"
        print(f"Cloud lease mint failed: {_last_admission_error}", file=sys.stderr)

        # A route miss means client/server disagreement, not a rejected key.
        if response.status_code == 404:
            _handle_unreachable(response)
            return None

        # HTTP 4xx (except 404 and 429): Terminal refusal
        if 400 <= response.status_code < 500 and response.status_code != 429:
            reason = f"{mint_url} -> HTTP {response.status_code}: {message}"
            _logger.warning("Cloud admission refusal HTTP %d on session=%s: %s", response.status_code, session_id, reason)
            if session_id:
                state.record_refusal(session_id, reason=reason, status_code=response.status_code)
            with _lock:
                _current_lease = None
                _current_lease_session_id = None
                _quiet_until = 0.0
            return None

        # HTTP 429 or 5xx: Unreachable
        _handle_unreachable(response)
        return None

    except Exception as exc:
        # Network errors / timeouts
        _last_admission_error = f"{mint_url} -> no response: {exc}"
        print(f"Cloud lease mint failed: {_last_admission_error}", file=sys.stderr)
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
        "Cloud admission backoff (%s); waiting %.1fs (attempt %d)",
        exc or f"HTTP {code}", wait, failures,
    )
