"""Session state management and checkpoint system.

FILE: snodo/infrastructure/session.py

Implements INV5 from Section 4.6 Runtime State.
Sessions are scoped to (mode, project). Tokens are deliberately
excluded - revalidation on resume is required.
"""

import json
import logging
import secrets
from datetime import datetime, UTC, timedelta
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict, field
from pathlib import Path

from snodo.infrastructure.paths import resolve_home
from snodo.project import get_project_id

_logger = logging.getLogger(__name__)


MODE_PREFIXES = {
    "producer": "prod",
    "reviewer": "rev",
    "planner": "plan",
}


def _mode_prefix(mode: str) -> str:
    """Get short prefix for mode in session IDs."""
    return MODE_PREFIXES.get(mode, mode[:4])



@dataclass
class Checkpoint:
    """Checkpoint data within a session (INV5: no tokens)."""
    current_task: Optional[str] = None
    decisions: Dict[str, Any] = field(default_factory=dict)
    memory_summary: str = ""
    timestamp: str = ""


@dataclass
class SessionState:
    """State of a protocol execution session.

    Scoped to (mode, project). Tokens are NOT persisted (INV5).
    Active session tracking is project-level via .snodo/state.json.
    All session files represent valid sessions — no status field.
    """
    session_id: str
    mode: str
    project_root: str
    project_id: str
    created_at: str
    updated_at: str
    checkpoint: Checkpoint


class SessionManager:
    """Manages session state and checkpointing.

    Sessions are stored globally at ~/.snodo/sessions/ (user-scoped).
    Each session file is a JSON file with complete SessionState.
    audit_log is injected via constructor (7.1 pattern).
    """

    def __init__(
        self,
        audit_log: Any = None,
        sessions_dir: Optional[Path] = None,
    ):
        """Initialize session manager.

        Args:
            audit_log: Optional AuditLog for event logging (constructor injection)
            sessions_dir: Override sessions directory (for test isolation)
        """
        self._audit_log = audit_log
        self.sessions_dir = sessions_dir or resolve_home() / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _audit(self, event_type: str, data: Dict[str, Any]) -> None:
        """Log an audit event if audit_log is available."""
        if self._audit_log is not None:
            self._audit_log.append_event(event_type, data)

    def create_session(
        self,
        mode: str,
        project_root: str,
    ) -> SessionState:
        """Create a new session.

        Args:
            mode: Protocol mode (producer, reviewer, planner)
            project_root: Absolute path to project root

        Returns:
            Created SessionState
        """
        now = datetime.now(UTC).isoformat()
        date_str = datetime.now(UTC).strftime("%Y%m%d")
        prefix = _mode_prefix(mode)
        rand_hex = secrets.token_hex(3)
        session_id = f"sess_{date_str}_{prefix}_{rand_hex}"

        session = SessionState(
            session_id=session_id,
            mode=mode,
            project_root=project_root,
            project_id=get_project_id(project_root)[0],
            created_at=now,
            updated_at=now,
            checkpoint=Checkpoint(timestamp=now),
        )

        self._save_session(session)
        self._audit("session_started", {
            "op": "session_started",
            "session_id": session_id,
            "mode": mode,
            "project_root": project_root,
        })
        # Set this session as active for its (project, mode)
        self._set_active_pointer(project_root, mode, session_id)
        return session

    def get_active_session(
        self,
        mode: str,
        project_root: str,
    ) -> Optional[SessionState]:
        """Return the active session for (project, mode).

        The authoritative source is the per-mode pointer in
        .snodo/state.json.  Falls back to auto-adoption when the
        pointer is unset or stale.

        Args:
            mode: Protocol mode
            project_root: Absolute path to project root

        Returns:
            Matching SessionState if found, None otherwise
        """
        from snodo.infrastructure.state import read_state

        state = read_state(project_root)
        pointer = state.active_session.get(mode)
        pid = get_project_id(project_root)[0]

        # Pointer set and valid → authoritative
        if pointer:
            try:
                session = self.load_session(pointer)
                if session.mode == mode and session.project_id == pid:
                    return session
            except FileNotFoundError:
                pass  # stale pointer — fall through to auto-adopt

        # No pointer or stale — find all sessions for this (project, mode)
        candidates: List[SessionState] = []
        for session_file in self.sessions_dir.glob("*.json"):
            try:
                s = self._load_file(session_file)
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            if s.mode == mode and s.project_id == pid:
                candidates.append(s)

        if not candidates:
            return None

        if len(candidates) == 1:
            # Exactly one — adopt it as active
            session = candidates[0]
            _logger.info(
                "Auto-adopting active session %s for mode=%s project=%s",
                session.session_id, mode, project_root,
            )
            self._set_active_pointer(project_root, mode, session.session_id)
            return session

        # Multiple candidates, no pointer — ambiguous.  Pick most-recent.
        candidates.sort(key=lambda s: s.updated_at, reverse=True)
        session = candidates[0]
        _logger.warning(
            "Multiple sessions (%d) for mode=%s project=%s, no active pointer. "
            "Adopting most-recent %s as active.",
            len(candidates), mode, project_root, session.session_id,
        )
        self._set_active_pointer(project_root, mode, session.session_id)
        return session

    def load_session(self, session_id: str) -> SessionState:
        """Load a session by ID.

        Args:
            session_id: Session identifier

        Returns:
            Loaded SessionState

        Raises:
            FileNotFoundError: If session file doesn't exist
        """
        session_path = self.sessions_dir / f"{session_id}.json"
        if not session_path.exists():
            raise FileNotFoundError(f"No session found: {session_id}")
        return self._load_file(session_path)

    def save_checkpoint(
        self,
        session_id: str,
        checkpoint: Optional[Checkpoint] = None,
    ) -> None:
        """Save checkpoint data for a session.

        Args:
            session_id: Session identifier
            checkpoint: Checkpoint data to save (updates existing if None)
        """
        session = self.load_session(session_id)
        if checkpoint is not None:
            session.checkpoint = checkpoint
        session.checkpoint.timestamp = datetime.now(UTC).isoformat()
        session.updated_at = datetime.now(UTC).isoformat()
        self._save_session(session)

    def update_decision(self, session_id: str, key: str, value: Any) -> None:
        """Update a decision in the session checkpoint.

        Args:
            session_id: Session identifier
            key: Decision key
            value: Decision value
        """
        session = self.load_session(session_id)
        session.checkpoint.decisions[key] = value
        session.updated_at = datetime.now(UTC).isoformat()
        self._save_session(session)
        self._audit("session_decision_updated", {
            "op": "session_decision_updated",
            "session_id": session_id,
            "key": key,
            "value": value,
        })

    def update_memory_summary(self, session_id: str, summary: str) -> None:
        """Update the memory summary in the session checkpoint.

        Args:
            session_id: Session identifier
            summary: Memory summary text
        """
        session = self.load_session(session_id)
        session.checkpoint.memory_summary = summary
        session.updated_at = datetime.now(UTC).isoformat()
        self._save_session(session)
        self._audit("session_memory_updated", {
            "op": "session_memory_updated",
            "session_id": session_id,
        })

    def set_current_task(self, session_id: str, task_id: Optional[str]) -> None:
        """Set the current task in the session checkpoint.

        Args:
            session_id: Session identifier
            task_id: Task identifier (or None to clear)
        """
        session = self.load_session(session_id)
        old_task = session.checkpoint.current_task
        session.checkpoint.current_task = task_id
        session.updated_at = datetime.now(UTC).isoformat()
        self._save_session(session)
        self._audit("session_task_changed", {
            "op": "session_task_changed",
            "session_id": session_id,
            "old_task": old_task,
            "new_task": task_id,
        })

    def set_active_session(
        self,
        project_root: str,
        mode: str,
        session_id: str,
    ) -> None:
        """Set *session_id* as the active session for (project, mode).

        Validates that the session exists and matches the (project, mode)
        scope before writing the pointer.  Raises FileNotFoundError or
        ValueError on mismatch.
        """
        session = self.load_session(session_id)
        if session.mode != mode:
            raise ValueError(
                f"Session {session_id} is mode={session.mode}, "
                f"not {mode}"
            )
        pid = get_project_id(project_root)[0]
        if session.project_id != pid:
            raise ValueError(
                f"Session {session_id} is for a different project"
            )
        self._set_active_pointer(project_root, mode, session_id)

    def _set_active_pointer(
        self, project_root: str, mode: str, session_id: str,
    ) -> None:
        """Write the active-session pointer to state.json (best-effort)."""
        from snodo.infrastructure.state import read_state, write_state

        try:
            state = read_state(project_root)
            state.active_session[mode] = session_id
            write_state(project_root, state)
        except (OSError, PermissionError) as e:
            _logger.warning(
                "Could not write active-session pointer for %s/%s: %s",
                project_root, mode, e,
            )

    def delete_session(self, session_id: str) -> None:
        """Delete a session file.  Clears the active pointer if deleted."""
        from snodo.infrastructure.state import read_state, write_state

        session = self.load_session(session_id)  # Validate exists
        self._audit("session_deleted", {
            "op": "session_deleted",
            "session_id": session_id,
        })
        (self.sessions_dir / f"{session_id}.json").unlink(missing_ok=True)

        # Clear active pointer if this was the active session
        try:
            state = read_state(session.project_root)
            pointer = state.active_session.get(session.mode)
            if pointer == session_id:
                del state.active_session[session.mode]
                write_state(session.project_root, state)
        except (OSError, PermissionError):
            pass

    def list_sessions(
        self,
        mode: Optional[str] = None,
        project_root: Optional[str] = None,
        status: Optional[str] = None,  # Deprecated — kept for API compat, ignored
    ) -> List[SessionState]:
        """List sessions with optional filters.

        Args:
            mode: Filter by mode
            project_root: Filter by project root
            status: Deprecated parameter, ignored (all sessions are valid)

        Returns:
            List of matching SessionState objects
        """
        pid = get_project_id(project_root)[0] if project_root else None
        results: List[SessionState] = []
        for session_file in sorted(self.sessions_dir.glob("*.json")):
            try:
                session = self._load_file(session_file)
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            if mode and session.mode != mode:
                continue
            if pid and session.project_id != pid:
                continue
            results.append(session)
        return results

    def prune_stale(self, max_age_days: int = 30) -> int:
        """Remove sessions older than max_age_days.

        The active session for each (project, mode) is NEVER pruned
        regardless of its age.

        Args:
            max_age_days: Maximum age in days before a session is stale

        Returns:
            Number of sessions pruned
        """
        cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
        pruned = 0

        # Collect active pointers from all known state.json files
        # to prevent pruning ANY active session.
        active_ids: set = set()
        for session_file in self.sessions_dir.glob("*.json"):
            try:
                s = self._load_file(session_file)
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            try:
                from snodo.infrastructure.state import read_state
                state = read_state(s.project_root)
                for sid in state.active_session.values():
                    active_ids.add(sid)
            except Exception:
                pass

        for session_file in list(self.sessions_dir.glob("*.json")):
            try:
                session = self._load_file(session_file)
            except (json.JSONDecodeError, KeyError, TypeError):
                session_file.unlink(missing_ok=True)
                pruned += 1
                continue

            if session.session_id in active_ids:
                continue

            try:
                updated = datetime.fromisoformat(session.updated_at)
            except (ValueError, TypeError):
                self._audit("session_deleted", {
                    "op": "session_deleted",
                    "session_id": session.session_id,
                })
                session_file.unlink(missing_ok=True)
                pruned += 1
                continue

            if updated < cutoff:
                self._audit("session_deleted", {
                    "op": "session_deleted",
                    "session_id": session.session_id,
                })
                session_file.unlink(missing_ok=True)
                pruned += 1
        return pruned

    def _save_session(self, session: SessionState) -> None:
        """Save session state to JSON file."""
        session_path = self.sessions_dir / f"{session.session_id}.json"
        data = asdict(session)
        with open(session_path, "w") as f:
            json.dump(data, f, indent=2)

    def _load_file(self, path: Path) -> SessionState:
        """Load session from a JSON file path.

        Handles legacy session files that may contain deprecated fields
        (status, parent_session).
        """
        with open(path) as f:
            data = json.load(f)
        # Strip deprecated fields for backward compatibility
        data.pop("parent_session", None)
        data.pop("status", None)
        checkpoint_data = data.pop("checkpoint", {})
        checkpoint = Checkpoint(**checkpoint_data)
        return SessionState(**data, checkpoint=checkpoint)
