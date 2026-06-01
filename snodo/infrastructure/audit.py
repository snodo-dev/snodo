"""Append-only audit log with cryptographic hash chain.

Implements INV4 from Section 4.5 Runtime State.
Events are immutable and form a verifiable chain.
Thread-safe: a single lock wraps both in-memory append and disk write.
"""

import hashlib
import json
import sys
import threading
import time
from datetime import datetime, UTC
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class AuditEvent:
    """Single event in the audit log."""
    sequence: int
    timestamp: str
    event_type: str
    data: Dict[str, Any]
    previous_hash: str
    event_hash: str


class AuditLog:
    """Append-only event logger with cryptographic hash chain.

    Events form a blockchain-style chain where each event's hash
    depends on the previous event's hash, ensuring immutability.

    Thread safety: a single Lock wraps BOTH the in-memory append
    and the disk write in one critical section.

    Disk failure handling: retry once after 100ms, then warn to
    stderr. No in-memory buffer (would create undetectable audit
    gaps on crash).
    """

    def __init__(self, log_path: str = ".snodo/audit.log"):
        """Initialize audit log.

        Args:
            log_path: Path to audit log file
        """
        self.log_path = Path(log_path)
        self.events: List[AuditEvent] = []
        self._lock = threading.Lock()
        self._load_existing_log()

    def append_event(self, event_type: str, data: Dict[str, Any]) -> AuditEvent:
        """Append a new event to the log.

        Thread-safe: acquires lock for both in-memory and disk write.

        Args:
            event_type: Type of event (e.g., "dispatch", "validate")
            data: Event data dictionary

        Returns:
            The created AuditEvent
        """
        with self._lock:
            sequence = len(self.events)
            timestamp = datetime.now(UTC).isoformat()
            previous_hash = self.events[-1].event_hash if self.events else "0" * 64

            event_hash = self._compute_hash(
                sequence, timestamp, event_type, data, previous_hash
            )

            event = AuditEvent(
                sequence=sequence,
                timestamp=timestamp,
                event_type=event_type,
                data=data,
                previous_hash=previous_hash,
                event_hash=event_hash,
            )

            self.events.append(event)
            self._safe_append_to_disk(event)

        return event

    def get_history(self, event_type: Optional[str] = None) -> List[AuditEvent]:
        """Get audit event history.

        Args:
            event_type: Optional filter by event type

        Returns:
            List of audit events (filtered if event_type provided)
        """
        if event_type is None:
            return self.events.copy()
        return [e for e in self.events if e.event_type == event_type]

    def verify_chain(self) -> bool:
        """Verify integrity of the hash chain.

        Returns:
            True if chain is valid, False if tampered
        """
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
            )
            if event.event_hash != expected_hash:
                return False

        return True

    def _compute_hash(
        self,
        sequence: int,
        timestamp: str,
        event_type: str,
        data: Dict[str, Any],
        previous_hash: str,
    ) -> str:
        """Compute cryptographic hash for an event."""
        payload = {
            "sequence": sequence,
            "timestamp": timestamp,
            "event_type": event_type,
            "data": data,
            "previous_hash": previous_hash,
        }
        payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload_bytes).hexdigest()

    def _load_existing_log(self) -> None:
        """Load existing log file if it exists."""
        if not self.log_path.exists():
            return

        try:
            with open(self.log_path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    event_dict = json.loads(line)
                    event = AuditEvent(**event_dict)
                    self.events.append(event)
        except Exception:
            pass

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
        """Append event to disk with retry-once and stderr fallback.

        No in-memory buffer — if disk write fails after retry,
        warn to stderr but do not raise.
        """
        try:
            self._append_to_disk(event)
        except Exception:
            # Retry once after 100ms
            time.sleep(0.1)
            try:
                self._append_to_disk(event)
            except Exception as e:
                print(
                    f"AUDIT WARNING: failed to persist event "
                    f"seq={event.sequence} type={event.event_type}: {e}",
                    file=sys.stderr,
                )


# Singleton instance for global audit log
_global_audit_log = None


def get_audit_log(log_path: str = ".snodo/audit.log") -> AuditLog:
    """Get global audit log instance.

    Args:
        log_path: Path to audit log file

    Returns:
        Global AuditLog instance
    """
    global _global_audit_log
    if _global_audit_log is None:
        _global_audit_log = AuditLog(log_path)
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
