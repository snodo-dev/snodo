"""Cloud sync infrastructure — cursor tracking + audit event dispatch.

FILE: snodo/infrastructure/cloud_sync.py

Manages per-session sync cursors (~/.snodo/cloud_sync.json) and
dispatches audit events to api.snodo.dev/ingest in background threads.

Contract (from snodo-cloud ADR):
  POST api.snodo.dev/ingest, Bearer auth, 1-50 events per batch,
  cursor advances on 200 only, 429 respects retry_after,
  5xx exponential backoff up to 5 retries, never raises.
"""

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
        """Record that events up to *sequence* have been synced."""
        data = self._load()
        if session_id not in data or not isinstance(data.get(session_id), dict):
            data[session_id] = {}
        data[session_id]["last_synced_sequence"] = sequence
        data[session_id]["last_synced_at"] = time.time()
        self._save(data)

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
    ) -> dict:
        """Sync audit events since the last cursor.

        Args:
            session_id: Current session identifier
            project_root: Absolute project path
            audit_log: AuditLog instance (provides .events)
            api_key: Snodo cloud API key
            api_url: Base URL for the ingest API

        Returns:
            ``{"synced": int, "failed": bool}``
        """
        try:
            return self._sync_impl(session_id, project_root, audit_log, api_key, api_url)
        except Exception:
            _logger.warning("Cloud sync threw unexpected exception", exc_info=True)
            return {"synced": 0, "failed": True}

    def _sync_impl(
        self,
        session_id: str,
        project_root: str,
        audit_log: Any,
        api_key: str,
        api_url: str,
    ) -> dict:
        events = getattr(audit_log, "events", [])
        if not events:
            return {"synced": 0, "failed": False}

        state = CloudSyncState()
        cursor = state.get_cursor(session_id)

        # Collect unsynced events
        unsynced: list = []
        for ev in events:
            if ev.sequence > cursor:
                unsynced.append(ev)

        if not unsynced:
            return {"synced": 0, "failed": False}

        synced = 0
        failed = False

        # Batch into groups of ≤50
        for i in range(0, len(unsynced), _MAX_BATCH_SIZE):
            batch = unsynced[i:i + _MAX_BATCH_SIZE]
            max_seq = batch[-1].sequence if batch else cursor
            ok = self._post_batch(
                session_id, project_root, batch, api_key, api_url,
            )
            if ok:
                state.advance_cursor(session_id, max_seq)
                synced += len(batch)
            else:
                failed = True
                break

        return {"synced": synced, "failed": failed}

    def _post_batch(
        self,
        session_id: str,
        project_root: str,
        batch: list,
        api_key: str,
        api_url: str,
    ) -> bool:
        """POST a batch of events. Returns True if cursor should advance."""
        import urllib.request
        import urllib.error

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

        for attempt in range(_MAX_RETRIES + 1):
            try:
                req = urllib.request.Request(
                    url, data=body, method="POST",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if resp.status == 200:
                        return True
                    # Unexpected 2xx — treat as success
                    return False

            except urllib.error.HTTPError as e:
                if e.code == 429:
                    retry_after = e.headers.get("Retry-After", "5")
                    try:
                        wait = int(retry_after)
                    except ValueError:
                        wait = 5
                    _logger.warning(
                        "Cloud sync 429 rate limited on session=%s, "
                        "retry_after=%s",
                        session_id, retry_after,
                    )
                    time.sleep(wait)
                    # 429 retry doesn't count against the backoff attempts
                    continue

                if 500 <= e.code < 600:
                    if attempt == _MAX_RETRIES:
                        _logger.warning(
                            "Cloud sync 5xx retries exhausted (session=%s, code=%d)",
                            session_id, e.code,
                        )
                        return False
                    backoff = 2 ** attempt
                    time.sleep(backoff)
                    continue

                _logger.warning(
                    "Cloud sync HTTP %d on session=%s — stopping",
                    e.code, session_id,
                )
                return False

            except Exception:
                if attempt == _MAX_RETRIES:
                    _logger.warning(
                        "Cloud sync network error retries exhausted (session=%s)",
                        session_id, exc_info=True,
                    )
                    return False
                backoff = 2 ** attempt
                time.sleep(backoff)

        return False


def _should_sync(config: Optional[dict] = None) -> bool:
    """Return True if cloud sync is enabled and an API key is configured."""
    if config is None:
        from snodo.cli.config import ConfigManager
        config = ConfigManager().load()
    cloud = config.get("cloud", {}) if isinstance(config, dict) else {}
    return bool(cloud.get("sync_enabled")) and bool(cloud.get("api_key", "").strip())


def sync_if_enabled(
    session_id: str,
    project_root: str,
    audit_log: Any,
    config: Optional[dict] = None,
) -> None:
    """Fire-and-forget cloud sync if enabled. Runs in a background thread."""
    from threading import Thread

    if not _should_sync(config):
        return

    if config is None:
        from snodo.cli.config import ConfigManager
        config = ConfigManager().load()

    cloud = config.get("cloud", {})
    api_key = cloud["api_key"]
    api_url = cloud["api_url"]

    dispatcher = CloudSyncDispatcher()
    thread = Thread(
        target=dispatcher.sync,
        args=(session_id, project_root, audit_log, api_key, api_url),
        daemon=True,
    )
    thread.start()
