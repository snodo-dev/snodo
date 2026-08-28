"""Append-only audit log with cryptographic hash chain.

Implements INV4 from Section 4.5 Runtime State.
Events are immutable and form a verifiable chain.
Thread-safe: a single lock wraps both in-memory append and disk write.
Process-safe: an exclusive file lock (``fcntl.flock``) serialises appends
across processes, and the next sequence is derived from the file's last line,
never from process-local memory (Fixes #114).
"""

import hashlib
import json
import threading
import time
from datetime import datetime, UTC
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
import os
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platform
    fcntl = None


from snodo.core.interfaces import AuditError


_RECOVERY_GUIDANCE = (
    "Inspect the log, truncate it deliberately, or archive it and start a "
    "new chain. snodo will not auto-repair the audit log."
)

#: How long append_event blocks waiting for the exclusive file lock before
#: failing loudly. The audit log is the attestation; a writer that cannot get
#: the lock must not silently proceed (Fixes #114).
_LOCK_TIMEOUT = 10.0


class _NullContext:
    """No-op context manager for platforms without ``fcntl``."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@dataclass
class AuditEvent:
    """Single event in the audit log."""
    sequence: int
    timestamp: str
    event_type: str
    project_id: str
    data: Dict[str, Any]
    previous_hash: str
    event_hash: str


class AuditLog:
    """Append-only event logger with cryptographic hash chain.

    Events form a blockchain-style chain where each event's hash
    depends on the previous event's hash, ensuring immutability.

    Thread safety: a single Lock wraps BOTH the in-memory append
    and the disk write in one critical section.

    Process safety: an exclusive ``fcntl.flock`` on the log file serialises
    appends across processes. The next sequence and previous hash are derived
    from the file's last line under the lock — never from process-local
    memory — so two processes appending concurrently cannot both write "their"
    next sequence (Fixes #114). The in-memory ``events`` list is a cache only.

    Lock acquisition: blocks up to ``_LOCK_TIMEOUT`` seconds for the exclusive
    lock, then raises ``AuditError``. It never silently proceeds without the
    lock — the audit log is the attestation.

    Disk failure handling: retry once after 100ms, then warn to
    stderr. No in-memory buffer (would create undetectable audit
    gaps on crash).
    """

    def __init__(self, log_path: str = ".snodo/audit.log", project_id: str = ""):
        """Initialize audit log.

        Args:
            log_path: Path to audit log file
            project_id: Optional project identifier
        """
        self.log_path = Path(log_path)
        self._project_id = project_id
        self.events: List[AuditEvent] = []
        self._lock = threading.Lock()
        self._load_ok = False
        self._load_existing_log()
        self._load_ok = True

    def append_event(self, event_type: str, data: Dict[str, Any]) -> AuditEvent:
        """Append a new event to the log.

        Thread-safe and process-safe: acquires the in-process lock, then an
        exclusive file lock (``fcntl.flock``), re-reads the file's last line
        to derive the true sequence and previous hash, writes, and releases.
        The in-memory ``events`` list is a cache only — it is never the source
        of the next sequence (Fixes #114).

        Args:
            event_type: Type of event (e.g., "dispatch", "validate")
            data: Event data dictionary

        Returns:
            The created AuditEvent

        Raises:
            AuditError: If disk write fails after retry, if the log was not
                loaded cleanly (appending onto an unverified chain is refused),
                or if the exclusive file lock cannot be acquired within
                ``_LOCK_TIMEOUT`` seconds.
        """
        with self._lock:
            if not self._load_ok:
                raise AuditError(
                    f"Refusing to append to audit log {self.log_path}: the log "
                    f"did not load cleanly. {_RECOVERY_GUIDANCE}"
                )
            with self._exclusive_file_lock():
                # Derive the true sequence from the file, not from memory: a
                # concurrent process may have appended since this instance
                # loaded (Fixes #114).
                sequence, previous_hash = self._read_tail_state()
                timestamp = datetime.now(UTC).isoformat()

                event_hash = self._compute_hash(
                    sequence, timestamp, event_type, data, previous_hash, self._project_id
                )

                event = AuditEvent(
                    sequence=sequence,
                    timestamp=timestamp,
                    event_type=event_type,
                    project_id=self._project_id,
                    data=data,
                    previous_hash=previous_hash,
                    event_hash=event_hash,
                )

                # Disk first — if this raises, memory stays consistent
                self._safe_append_to_disk(event)
                self.events.append(event)

        return event

    def get_history(self, event_type: Optional[str] = None) -> List[AuditEvent]:
        """Get audit event history (consistent snapshot under lock).

        Args:
            event_type: Optional filter by event type

        Returns:
            List of audit events (filtered if event_type provided)
        """
        with self._lock:
            if event_type is None:
                return self.events.copy()
            return [e for e in self.events if e.event_type == event_type]

    def verify_chain(self) -> bool:
        """Verify integrity of the hash chain.

        Validates the in-memory chain and confirms it agrees with the file on
        disk. If the file contains events beyond those loaded into memory (for
        example, after a truncated load), the chain is reported invalid rather
        than certifying a record that no longer matches disk.

        Returns:
            True if chain is valid, False if tampered or file/memory diverge
        """
        if not self._load_ok:
            return False

        if not self.events:
            return True

        if self.events[0].previous_hash != "0" * 64:
            return False

        for i, event in enumerate(self.events):
            if event.sequence != i:
                return False

            if i > 0:
                if event.previous_hash != self.events[i - 1].event_hash:
                    return False

            expected_hash = self._compute_hash(
                event.sequence,
                event.timestamp,
                event.event_type,
                event.data,
                event.previous_hash,
                event.project_id,
            )
            if event.event_hash != expected_hash:
                return False

        if not self._file_matches_memory():
            return False

        return True

    def _file_matches_memory(self) -> bool:
        """Return True if the on-disk log contains exactly the loaded events.

        Re-reads the file and compares each line's event hash against the
        in-memory chain. Detects events on disk that are not represented in
        memory (e.g. after a truncated load) as well as a file that has been
        truncated or altered since load.
        """
        if not self.log_path.exists():
            return len(self.events) == 0

        try:
            with open(self.log_path) as f:
                lines = [line for line in f if line.strip()]
        except OSError:
            return False

        if len(lines) != len(self.events):
            return False

        for i, line in enumerate(lines):
            try:
                event_dict = json.loads(line)
            except json.JSONDecodeError:
                return False
            if event_dict.get("event_hash") != self.events[i].event_hash:
                return False

        return True

    def _compute_hash(
        self,
        sequence: int,
        timestamp: str,
        event_type: str,
        data: Dict[str, Any],
        previous_hash: str,
        project_id: str = "",
    ) -> str:
        """Compute cryptographic hash for an event."""
        payload = {
            "sequence": sequence,
            "timestamp": timestamp,
            "event_type": event_type,
            "project_id": project_id,
            "data": data,
            "previous_hash": previous_hash,
        }
        payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload_bytes).hexdigest()

    def _load_existing_log(self) -> None:
        """Load existing log file if it exists.

        Fails loud on any malformed line, hash mismatch, or sequence
        discontinuity — consistent with the append path, which raises
        ``AuditError`` rather than silently dropping data. ``self.events`` is
        only assigned once the whole file has been validated, so a failed load
        leaves the object unusable for appends (see ``append_event``).

        Raises:
            AuditError: If the log cannot be parsed or its chain is broken,
                naming the offending line number and the log path.
        """
        if not self.log_path.exists():
            return

        loaded: List[AuditEvent] = []
        try:
            with open(self.log_path) as f:
                for line_no, line in enumerate(f, start=1):
                    if not line.strip():
                        continue
                    try:
                        event_dict = json.loads(line)
                    except json.JSONDecodeError as err:
                        raise AuditError(
                            f"Failed to load audit log {self.log_path}: "
                            f"malformed JSON on line {line_no}: {err}. "
                            f"{_RECOVERY_GUIDANCE}"
                        ) from err

                    if "project_id" not in event_dict:
                        event_dict["project_id"] = ""

                    try:
                        event = AuditEvent(**event_dict)
                    except TypeError as err:
                        raise AuditError(
                            f"Failed to load audit log {self.log_path}: "
                            f"invalid event on line {line_no}: {err}. "
                            f"{_RECOVERY_GUIDANCE}"
                        ) from err

                    if event.sequence != len(loaded):
                        raise AuditError(
                            f"Failed to load audit log {self.log_path}: "
                            f"sequence discontinuity on line {line_no} "
                            f"(expected {len(loaded)}, got {event.sequence}). "
                            f"{_RECOVERY_GUIDANCE}"
                        )

                    expected_previous = (
                        loaded[-1].event_hash if loaded else "0" * 64
                    )
                    if event.previous_hash != expected_previous:
                        raise AuditError(
                            f"Failed to load audit log {self.log_path}: "
                            f"hash mismatch on line {line_no} "
                            f"(previous_hash does not match the prior event). "
                            f"{_RECOVERY_GUIDANCE}"
                        )

                    expected_hash = self._compute_hash(
                        event.sequence,
                        event.timestamp,
                        event.event_type,
                        event.data,
                        event.previous_hash,
                        event.project_id,
                    )
                    if event.event_hash != expected_hash:
                        raise AuditError(
                            f"Failed to load audit log {self.log_path}: "
                            f"event hash mismatch on line {line_no}. "
                            f"{_RECOVERY_GUIDANCE}"
                        )

                    loaded.append(event)
        except OSError as err:
            raise AuditError(
                f"Failed to load audit log {self.log_path}: {err}. "
                f"{_RECOVERY_GUIDANCE}"
            ) from err

        self.events = loaded

    def _exclusive_file_lock(self):
        """Return a context manager holding an exclusive lock on the log file.

        Blocks up to ``_LOCK_TIMEOUT`` seconds for the lock, then raises
        ``AuditError`` — a writer that cannot get the lock must not silently
        proceed (Fixes #114). On platforms without ``fcntl`` (non-POSIX) the
        lock degrades to the in-process lock only; the file is still re-read
        for the true sequence, so cross-process safety is best-effort there.
        """
        if fcntl is None:  # pragma: no cover - non-POSIX platform
            return _NullContext()

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(self.log_path, "a")
        try:
            deadline = time.monotonic() + _LOCK_TIMEOUT
            while True:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        f.close()
                        raise AuditError(
                            f"Could not acquire the exclusive lock on audit log "
                            f"{self.log_path} within {_LOCK_TIMEOUT:.0f}s. "
                            "Another snodo process is holding it. Refusing to "
                            "append rather than risk a broken hash chain. "
                            f"{_RECOVERY_GUIDANCE}"
                        )
                    time.sleep(0.01)
        except Exception:
            f.close()
            raise

        class _Locked:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                finally:
                    f.close()
                return False

        return _Locked()

    def _read_tail_state(self) -> tuple:
        """Return ``(sequence, previous_hash)`` from the file's last line.

        Re-reads the log under the exclusive lock so the next event chains off
        whatever the last writer actually persisted — never off process-local
        memory (Fixes #114). An empty file yields ``(0, "0"*64)``.
        """
        if not self.log_path.exists() or self.log_path.stat().st_size == 0:
            return 0, "0" * 64
        last_line = ""
        with open(self.log_path) as f:
            for line in f:
                if line.strip():
                    last_line = line
        if not last_line.strip():
            return 0, "0" * 64
        try:
            event_dict = json.loads(last_line)
        except json.JSONDecodeError as err:
            raise AuditError(
                f"Failed to read the tail of audit log {self.log_path}: "
                f"malformed JSON: {err}. {_RECOVERY_GUIDANCE}"
            ) from err
        return event_dict.get("sequence", 0) + 1, event_dict.get("event_hash", "0" * 64)

    def _append_to_disk(self, event: AuditEvent) -> None:
        """Append event to log file (raw, no retry).

        Args:
            event: Event to append
        """
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a") as f:
            event_dict = asdict(event)
            f.write(json.dumps(event_dict) + "\n")

    def _safe_append_to_disk(self, event: AuditEvent) -> None:
        """Append event to disk with retry-once, then raise AuditError.

        No in-memory buffer — if disk write fails after retry,
        the caller receives AuditError and decides whether to halt.
        """
        try:
            self._append_to_disk(event)
        except Exception:
            # Retry once after 100ms
            time.sleep(0.1)
            try:
                self._append_to_disk(event)
            except Exception as retry_err:
                raise AuditError(
                    f"Failed to persist audit event seq={event.sequence} "
                    f"type={event.event_type}: {retry_err}"
                ) from retry_err


# Singleton instance for global audit log
_global_audit_log = None


def reset_global_audit_log() -> None:
    """Reset the global audit log singleton (primarily for testing)."""
    global _global_audit_log
    _global_audit_log = None


def get_audit_log(log_path: Optional[str] = None, project_id: str = "") -> AuditLog:
    """Get global audit log instance.

    Args:
        log_path: Path to audit log file (defaults to ``.snodo/audit.log``,
            resolved against the project root — the audit log is a property
            of the PROJECT, not the user, so SNODO_HOME does not redirect it).
            ``SNODO_AUDIT_LOG`` overrides the default for test isolation only.
        project_id: Optional project identifier
    """
    global _global_audit_log
    if _global_audit_log is None:
        if log_path is None or log_path == ".snodo/audit.log":
            override = os.environ.get("SNODO_AUDIT_LOG")
            if override:
                log_path = override
            else:
                log_path = ".snodo/audit.log"
        _global_audit_log = AuditLog(log_path, project_id=project_id)
    return _global_audit_log


def log_event(event_type: str, data: Dict[str, Any]) -> AuditEvent:
    """Log an event to the global audit log (convenience function).

    Args:
        event_type: Type of event
        data: Event data

    Returns:
        Created AuditEvent
    """
    return get_audit_log().append_event(event_type, data)
