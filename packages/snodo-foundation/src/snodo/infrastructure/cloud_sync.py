"""Cloud sync infrastructure — cursor tracking + audit event dispatch.

FILE: snodo/infrastructure/cloud_sync.py

Manages per-session sync cursors (~/.snodo/cloud_sync.json) and
dispatches audit events to api.snodo.dev/ingest in background threads.

Contract (from snodo-cloud ADR):
  POST api.snodo.dev/ingest, Bearer auth, 1-50 events per batch,
  cursor advances on 200 only, 429 respects retry_after,
  5xx exponential backoff up to 5 retries, never raises.
"""

import atexit
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from snodo.infrastructure.paths import resolve_home

_logger = logging.getLogger(__name__)

_MAX_BATCH_SIZE = 50
_MAX_RETRIES = 5


class CloudSyncState:
    """Tracks per-session sync progress in ~/.snodo/cloud_sync.json.

    Atomic writes (tmp + rename), matching the agents.json pattern.
    """

    def __init__(self, state_path: Optional[Path] = None):
        self._path = state_path or resolve_home() / "cloud_sync.json"

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        os.replace(str(tmp), str(self._path))

    def get_cursor(self, session_id: str) -> int:
        """Return last_synced_sequence for *session_id* (0 if never synced)."""
        data = self._load()
        session = data.get(session_id)
        if isinstance(session, dict):
            return session.get("last_synced_sequence", 0)
        return 0

    def advance_cursor(self, session_id: str, sequence: int) -> None:
        """Record that events up to *sequence* have been synced.

        A confirmed success clears a refusal, the pending count, and any
        recorded error, so ``cloud status`` reflects a healthy session
        (Fixes #141, #142).
        """
        data = self._load()
        if session_id not in data or not isinstance(data.get(session_id), dict):
            data[session_id] = {}
        sess = data[session_id]
        sess["last_synced_sequence"] = sequence
        sess["last_synced_at"] = time.time()
        sess["refused"] = False
        sess.pop("refused_reason", None)
        sess.pop("refused_range", None)
        sess.pop("refused_at", None)
        sess.pop("refused_status_code", None)
        sess["pending_count"] = 0
        sess.pop("last_error", None)
        self._save(data)

    def record_attempt(
        self, session_id: str, pending: int, error: Optional[str] = None,
    ) -> None:
        """Record a sync attempt outcome for *session_id* (Fixes #142).

        Stores how many events were pending, when the attempt happened, and
        what went wrong (if it failed) so ``cloud status`` can answer "is my
        audit trail actually reaching the cloud?".
        """
        data = self._load()
        if session_id not in data or not isinstance(data.get(session_id), dict):
            data[session_id] = {}
        data[session_id]["pending_count"] = pending
        data[session_id]["last_attempt_at"] = time.time()
        if error is not None:
            data[session_id]["last_error"] = error
        self._save(data)

    def record_refusal(
        self,
        session_id: str,
        reason: str,
        first_seq: int,
        last_seq: int,
        status_code: Optional[int] = None,
    ) -> None:
        """Record that a batch for *session_id* was refused by the cloud server."""
        data = self._load()
        if session_id not in data or not isinstance(data.get(session_id), dict):
            data[session_id] = {}
        sess = data[session_id]
        sess["refused"] = True
        sess["refused_reason"] = reason
        sess["refused_range"] = [first_seq, last_seq]
        sess["refused_at"] = time.time()
        if status_code is not None:
            sess["refused_status_code"] = status_code
        self._save(data)

    def clear_refusal(self, session_id: str) -> None:
        """Clear refused status for *session_id*."""
        data = self._load()
        if session_id in data and isinstance(data[session_id], dict):
            sess = data[session_id]
            sess["refused"] = False
            sess.pop("refused_reason", None)
            sess.pop("refused_range", None)
            sess.pop("refused_at", None)
            sess.pop("refused_status_code", None)
            self._save(data)

    def is_refused(self, session_id: str) -> bool:
        """Return True if *session_id* sync is currently refused."""
        data = self._load()
        sess = data.get(session_id)
        if isinstance(sess, dict):
            return bool(sess.get("refused"))
        return False

    def get_summary(self) -> dict:
        """Return full per-session sync summary."""
        return self._load()


class CloudSyncDispatcher:
    """Dispatches unsynced audit events to snodo cloud.

    Runs in a background thread — never blocks the caller, never raises.
    """

    def sync(
        self,
        session_id: str,
        project_root: str,
        audit_log: Any,
        api_key: str,
        api_url: str,
        force: bool = False,
    ) -> dict:
        """Sync audit events since the last cursor.

        Args:
            session_id: Current session identifier
            project_root: Absolute project path
            audit_log: AuditLog instance (provides .events)
            api_key: Snodo cloud API key
            api_url: Base URL for the ingest API
            force: If True, re-attempt sync even if session is in refused state

        Returns:
            ``{"synced": int, "failed": bool, "refused": bool, "reason": str,
                "pending": int}``
        """
        try:
            return self._sync_impl(session_id, project_root, audit_log, api_key, api_url, force=force)
        except Exception:
            _logger.warning("Cloud sync threw unexpected exception", exc_info=True)
            return {"synced": 0, "failed": True, "pending": 0}

    def _sync_impl(
        self,
        session_id: str,
        project_root: str,
        audit_log: Any,
        api_key: str,
        api_url: str,
        force: bool = False,
    ) -> dict:
        events = getattr(audit_log, "events", [])
        if not events:
            return {"synced": 0, "failed": False, "pending": 0}

        state = CloudSyncState()

        if not force and state.is_refused(session_id):
            info = state._load().get(session_id, {})
            reason = info.get("refused_reason", "refused by server")
            _logger.info(
                "Skipping automatic cloud sync for refused session %s: %s",
                session_id, reason,
            )
            return {"synced": 0, "failed": False, "refused": True, "reason": reason, "pending": 0}

        cursor = state.get_cursor(session_id)

        # Collect unsynced events
        unsynced: list = []
        for ev in events:
            if ev.sequence > cursor:
                unsynced.append(ev)

        if not unsynced:
            return {"synced": 0, "failed": False, "pending": 0}

        synced = 0
        failed = False
        refused = False
        refused_reason = None
        last_error: Optional[str] = None

        # Batch into groups of ≤50
        for i in range(0, len(unsynced), _MAX_BATCH_SIZE):
            batch = unsynced[i:i + _MAX_BATCH_SIZE]
            first_seq = batch[0].sequence
            max_seq = batch[-1].sequence
            outcome, reason, status_code = self._post_batch(
                session_id, project_root, batch, api_key, api_url,
            )

            if outcome == "delivered":
                state.advance_cursor(session_id, max_seq)
                _logger.debug("Cursor advanced to sequence %d", max_seq)
                synced += len(batch)
            elif outcome == "refused":
                state.record_refusal(
                    session_id,
                    reason=reason,
                    first_seq=first_seq,
                    last_seq=max_seq,
                    status_code=status_code,
                )
                failed = True
                refused = True
                refused_reason = reason
                last_error = reason
                break
            else:  # retryable
                failed = True
                last_error = reason
                break

        pending = len(unsynced) - synced
        state.record_attempt(session_id, pending=pending, error=last_error if failed else None)

        res_dict: dict = {"synced": synced, "failed": failed, "pending": pending}
        if refused:
            res_dict["refused"] = True
            res_dict["reason"] = refused_reason
        return res_dict

    def _post_batch(
        self,
        session_id: str,
        project_root: str,
        batch: list,
        api_key: str,
        api_url: str,
    ) -> tuple:
        """POST a batch of events.

        Returns:
            (outcome, reason, status_code) where outcome is one of:
            - "delivered": HTTP 2xx
            - "retryable": HTTP 429, 5xx, or network error
            - "refused": HTTP 4xx (except 429)
        """
        import httpx

        payload_events = []
        for ev in batch:
            payload_events.append({
                "sequence": ev.sequence,
                "timestamp": ev.timestamp,
                "event_type": ev.event_type,
                "data": ev.data,
                "previous_hash": ev.previous_hash,
                "event_hash": ev.event_hash,
            })

        body = json.dumps({
            "session_id": session_id,
            "project_path": project_root,
            "events": payload_events,
        }).encode()

        url = f"{api_url.rstrip('/')}/ingest"
        first_seq = batch[0].sequence
        last_seq = batch[-1].sequence
        _logger.debug(
            "POST %s — %d events (seq %d-%d)",
            url, len(batch), first_seq, last_seq,
        )
        _logger.debug("Authorization: Bearer %s...", api_key[:16])

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = httpx.post(
                    url, content=body, headers=headers, timeout=30.0,
                )

                if 200 <= response.status_code < 300:
                    _logger.debug("Response %d — accepted=%s",
                                  response.status_code, response.text[:200])
                    return ("delivered", f"HTTP {response.status_code}", response.status_code)

                body_text = response.text[:500]

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After", "5")
                    try:
                        wait = int(retry_after)
                    except ValueError:
                        wait = 5
                    _logger.warning(
                        "Cloud sync HTTP 429 retry_after=%s (session=%s): %s",
                        retry_after, session_id, body_text,
                    )
                    time.sleep(wait)
                    continue

                if response.status_code >= 500:
                    if attempt == _MAX_RETRIES:
                        _logger.warning(
                            "Cloud sync HTTP %d retries exhausted (session=%s): %s",
                            response.status_code, session_id, body_text,
                        )
                        return ("retryable", f"HTTP {response.status_code}: {body_text}", response.status_code)
                    _logger.warning(
                        "Cloud sync HTTP %d attempt %d (session=%s): %s",
                        response.status_code, attempt, session_id, body_text,
                    )
                    backoff = 2 ** attempt
                    time.sleep(backoff)
                    continue

                reason = f"HTTP {response.status_code}: {body_text.strip() or 'Client error'}"
                _logger.warning(
                    "Cloud sync HTTP %d REFUSED on session=%s: %s",
                    response.status_code, session_id, body_text,
                )
                return ("refused", reason, response.status_code)

            except Exception as exc:
                if attempt == _MAX_RETRIES:
                    _logger.warning(
                        "Cloud sync network error retries exhausted (session=%s): %s",
                        session_id, exc, exc_info=True,
                    )
                    return ("retryable", f"Network error: {exc}", None)
                backoff = 2 ** attempt
                time.sleep(backoff)

        return ("retryable", "Retries exhausted", None)


def _should_sync(config: Optional[dict] = None) -> bool:
    """Return True if cloud sync is enabled and an API key is configured."""
    if config is None:
        from snodo.config import ConfigManager
        config = ConfigManager().load()
    cloud = config.get("cloud", {}) if isinstance(config, dict) else {}
    return bool(cloud.get("sync_enabled")) and bool(cloud.get("api_key", "").strip())


#: How long the flush waits for background syncs to finish before giving up.
#: Bounded, always — a slow or unreachable cloud must never hang the process.
#: The whole flush fits inside one budget, however many syncs are pending; a
#: sync that needs longer is abandoned, the cursor is left where it was, and
#: the operator is told on stderr (Fixes #142).
_SYNC_WAIT_BUDGET = 5.0

#: Pending background syncs registered by ``sync_if_enabled``, drained once at
#: process exit by ``flush_pending_syncs``. Each entry carries the thread, its
#: result dict, the session id, and the audit log for the abandoned-case count.
_pending_syncs: list = []


def _pending_count(audit_log: Any, session_id: str) -> int:
    """Return the number of unsynced events for *session_id*.

    The unsynced backlog — events past the cursor — not the size of the whole
    log. This is the same measurement the sync itself uses, so the timeout and
    failure branches of the flush report the same thing (Fixes #142).
    """
    events = getattr(audit_log, "events", [])
    if not events:
        return 0
    cursor = CloudSyncState().get_cursor(session_id)
    return sum(1 for ev in events if ev.sequence > cursor)


def flush_pending_syncs() -> None:
    """Join pending background syncs with a single bounded wait and report.

    Registered via ``atexit`` so a sync that would succeed in a few seconds
    gets those seconds at process exit, exactly once — not once per task in a
    multi-task plan. The whole flush fits inside one ``_SYNC_WAIT_BUDGET``
    regardless of how many syncs are pending: threads are joined in turn, each
    for at most the remaining budget, and anything still running when the
    budget is spent is abandoned and reported. The wait is always bounded:
    threads stay daemon, so a slow or unreachable cloud never hangs the
    process. A sync that fails, or that is abandoned because it ran out of
    time, is reported on stderr in one line and the cursor is left where it
    was (events re-send next time) (Fixes #142).
    """
    import sys

    deadline = time.monotonic() + _SYNC_WAIT_BUDGET

    while _pending_syncs:
        thread, result, session_id, audit_log = _pending_syncs.pop(0)
        if thread.is_alive():
            remaining = deadline - time.monotonic()
            if remaining > 0:
                thread.join(timeout=remaining)
        if thread.is_alive():
            pending = _pending_count(audit_log, session_id)
            print(
                f"⚠ cloud sync still in progress for {session_id} — "
                f"{pending} event(s) pending; run `snodo cloud sync --all` to finish.",
                file=sys.stderr,
            )
            continue
        if result.get("failed"):
            pending = result.get("pending", _pending_count(audit_log, session_id))
            print(
                f"⚠ cloud sync failed for {session_id} — "
                f"{pending} event(s) pending; run `snodo cloud sync --all` to retry.",
                file=sys.stderr,
            )


atexit.register(flush_pending_syncs)


def sync_if_enabled(
    session_id: str,
    project_root: str,
    audit_log: Any,
    config: Optional[dict] = None,
) -> None:
    """Start a best-effort cloud sync if enabled and register it for the
    bounded wait at process exit (Fixes #142).

    The sync runs in a daemon thread and is joined with a bounded timeout by
    ``flush_pending_syncs`` at exit — once per process, whichever task started
    it. The CLI itself never blocks, and a slow cloud never hangs the process.
    """
    from threading import Thread

    if not _should_sync(config):
        return

    if config is None:
        from snodo.config import ConfigManager
        config = ConfigManager().load()

    cloud = config.get("cloud", {})
    api_key = cloud["api_key"]
    api_url = cloud["api_url"]

    dispatcher = CloudSyncDispatcher()
    result: dict = {"synced": 0, "failed": False, "pending": 0}

    def _run_sync():
        try:
            result.update(dispatcher.sync(session_id, project_root, audit_log, api_key, api_url))
        except Exception as e:
            _logger.warning("Cloud sync background thread failed: %s", e)
            result["failed"] = True

    thread = Thread(target=_run_sync, daemon=True)
    thread.start()
    _pending_syncs.append((thread, result, session_id, audit_log))
