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
from snodo.project import cache_project_id, get_project_id

_logger = logging.getLogger(__name__)


class SessionError(Exception):
    """Session state is corrupt or unreadable."""


# Exceptions that indicate a session file is corrupt/unreadable rather than
# absent. Used to surface corruption (warn + audit) instead of silently
# skipping, and to fail loud when resolving the active session.
_CORRUPT_SESSION_EXCEPTIONS = (
    json.JSONDecodeError, KeyError, TypeError, AttributeError, UnicodeDecodeError,
)


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

    def _warn_corrupt(self, path: Path, exc: Exception) -> None:
        """Warn and audit a corrupt session file instead of skipping silently."""
        _logger.warning("Skipping corrupt session file %s: %s", path, exc)
        self._audit("session_corrupt", {
            "op": "session_corrupt",
            "session_file": str(path),
            "error": str(exc),
        })

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

        pid, scope = get_project_id(project_root)
        # Creating a session is an act of establishing identity, not merely
        # reading it: persist the resolved id so a remote-less project's local
        # uuid survives between runs (get_project_id itself is read-only).
        cache_project_id(project_root, pid, scope)

        session = SessionState(
            session_id=session_id,
            mode=mode,
            project_root=project_root,
            project_id=pid,
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
        # Announce the project once per session so a consumer reading only the
        # event stream can materialise the project row without the transport
        # envelope (Fixes #214). The identity is resolved above; the payload is
        # identity, scope and display name — no paths, no machine details.
        self._audit("project_announced", {
            "op": "project_announced",
            "project_id": pid,
            "scope": scope,
            "display_name": Path(project_root).name,
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
            except FileNotFoundError:
                # An audited-but-missing ACTIVE pointer is a divergence (the
                # audit chain asserts this session ran under a different home).
                # Surface it instead of silently auto-adopting a session that
                # may not have been the one this project's runs actually used.
                if self.is_audited_but_missing(pointer, project_root):
                    _logger.warning(
                        "Active session pointer %s for mode=%s project=%s is "
                        "cited by the audit log but has no file under %s — it "
                        "was created under a different SNODO_HOME. Continuing "
                        "with auto-adoption, which may select a different "
                        "session than the one that actually ran.",
                        pointer, mode, project_root, self.sessions_dir,
                    )
                    self._audit("session_pointer_audited_but_missing", {
                        "op": "session_pointer_audited_but_missing",
                        "session_id": pointer,
                        "mode": mode,
                        "project_root": project_root,
                        "sessions_dir": str(self.sessions_dir),
                    })
                # stale pointer — fall through to auto-adopt
            except _CORRUPT_SESSION_EXCEPTIONS as exc:
                raise SessionError(
                    f"Active session {pointer} for mode={mode} "
                    f"project={project_root} is corrupt: {exc}"
                ) from exc
            else:
                if session.mode == mode and session.project_id == pid:
                    return session

        # No pointer or stale — find all sessions for this (project, mode)
        candidates: List[SessionState] = []
        for session_file in self.sessions_dir.glob("*.json"):
            try:
                s = self._load_file(session_file)
            except _CORRUPT_SESSION_EXCEPTIONS as exc:
                self._warn_corrupt(session_file, exc)
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
        session_path = self.sessions_dir / f"{session_id}.json"
        if not session_path.exists():
            raise FileNotFoundError(f"No session found: {session_id}")

        from snodo.infrastructure.state import atomic_update_json

        def _updater(data: Dict[str, Any]) -> None:
            now = datetime.now(UTC).isoformat()
            if checkpoint is not None:
                data["checkpoint"] = asdict(checkpoint)
            else:
                data.setdefault("checkpoint", {})
            data["checkpoint"]["timestamp"] = now
            data["updated_at"] = now

        atomic_update_json(
            self.sessions_dir, f"{session_id}.json", _updater, strict=True,
        )

    def update_decision(self, session_id: str, key: str, value: Any) -> None:
        """Update a decision in the session checkpoint.

        Args:
            session_id: Session identifier
            key: Decision key
            value: Decision value
        """
        session_path = self.sessions_dir / f"{session_id}.json"
        if not session_path.exists():
            raise FileNotFoundError(f"No session found: {session_id}")

        from snodo.infrastructure.state import atomic_update_json

        def _updater(data: Dict[str, Any]) -> None:
            checkpoint = data.setdefault("checkpoint", {})
            decisions = checkpoint.setdefault("decisions", {})
            decisions[key] = value
            now = datetime.now(UTC).isoformat()
            checkpoint["timestamp"] = now
            data["updated_at"] = now

        atomic_update_json(
            self.sessions_dir, f"{session_id}.json", _updater, strict=True,
        )
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
        session_path = self.sessions_dir / f"{session_id}.json"
        if not session_path.exists():
            raise FileNotFoundError(f"No session found: {session_id}")

        from snodo.infrastructure.state import atomic_update_json

        def _updater(data: Dict[str, Any]) -> None:
            checkpoint = data.setdefault("checkpoint", {})
            checkpoint["memory_summary"] = summary
            now = datetime.now(UTC).isoformat()
            checkpoint["timestamp"] = now
            data["updated_at"] = now

        atomic_update_json(
            self.sessions_dir, f"{session_id}.json", _updater, strict=True,
        )
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
        session_path = self.sessions_dir / f"{session_id}.json"
        if not session_path.exists():
            raise FileNotFoundError(f"No session found: {session_id}")

        from snodo.infrastructure.state import atomic_update_json

        old_task_holder = [None]

        def _updater(data: Dict[str, Any]) -> None:
            checkpoint = data.setdefault("checkpoint", {})
            old_task_holder[0] = checkpoint.get("current_task")
            checkpoint["current_task"] = task_id
            data["updated_at"] = datetime.now(UTC).isoformat()

        atomic_update_json(
            self.sessions_dir, f"{session_id}.json", _updater, strict=True,
        )
        self._audit("session_task_changed", {
            "op": "session_task_changed",
            "session_id": session_id,
            "old_task": old_task_holder[0],
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

    def audited_session_ids(
        self,
        project_root: str,
    ) -> tuple:
        """Return (present, missing) session-file paths/id sets vs the audit log.

        The audit log is a property of the PROJECT (``<project_root>/.snodo/
        audit.log``, never redirected by SNODO_HOME — Fixes #111), while session
        files are stored under THIS manager's sessions_dir (home-scoped). A
        session id can therefore appear in the audit log with no file under this
        store when it was created under a different snodo home. This method makes
        that divergence a detectable condition instead of a silent one: the audit
        chain says the run happened, and the caller can learn the id exists but
        its file is not here.

        The ids come from ``session_started`` / ``session_resumed`` events and
        from any other event carrying a ``session_id`` in its data, so an id is
        never hidden just because its ``session_started`` line is absent.

        The audit log is scanned raw (line by line) rather than re-validated
        through the AuditLog loader; the caller only needs the set of audited
        ids, not a full chain verification on every invocation.

        Returns:
            ``(present, missing)`` where ``present`` is the list of existing
            session file paths cited by the audit log and ``missing`` the list
            of audited session ids with no file under this store.
        """
        audit_path = Path(project_root) / ".snodo" / "audit.log"
        if not audit_path.exists():
            return [], []

        ids: set = set()
        deleted_ids: set = set()
        try:
            with open(audit_path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict):
                        data = event.get("data") or {}
                        sid = data.get("session_id")
                        if isinstance(sid, str) and sid:
                            if event.get("event") == "session_deleted" or data.get("op") == "session_deleted":
                                deleted_ids.add(sid)
                            else:
                                ids.add(sid)
        except OSError:
            return [], []

        candidate_ids = ids - deleted_ids

        present: List[Path] = []
        missing: List[str] = []
        for sid in sorted(candidate_ids):
            path = self.sessions_dir / f"{sid}.json"
            if path.exists():
                present.append(path)
            else:
                missing.append(sid)

        if missing:
            _logger.warning(
                "Audit log %s cites %d session id(s) with no file under %s: %s",
                audit_path, len(missing), self.sessions_dir, ", ".join(missing),
            )
            self._audit("session_audited_but_missing", {
                "op": "session_audited_but_missing",
                "project_root": project_root,
                "sessions_dir": str(self.sessions_dir),
                "session_ids": missing,
            })
        return present, missing

    def audited_missing_ids(self, project_root: str) -> List[str]:
        """Return audited session ids with no file under this store.

        Convenience wrapper over ``audited_session_ids`` returning just the
        missing ids (see that method for why this can happen).
        """
        _, missing = self.audited_session_ids(project_root)
        return missing

    def is_audited_but_missing(
        self,
        session_id: str,
        project_root: str,
    ) -> bool:
        """Return True when *session_id* is cited by the audit log but absent here.

        Use in the single-shot "Session not found" path (``snodo session show``,
        ``snodo task show``, ``snodo cloud sync --session``): turn a bare
        "Session not found" into a diagnosable divergence when the audit chain
        contradictorily asserts the session existed.

        The audit log is scanned raw (line by line) rather than loaded through
        the AuditLog validator, which would re-verify the whole chain on a
        per-lookup path.
        """
        audit_path = Path(project_root) / ".snodo" / "audit.log"
        if not audit_path.exists():
            return False
        cited = False
        deleted = False
        try:
            with open(audit_path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    data = event.get("data") or {}
                    if data.get("session_id") == session_id:
                        if event.get("event") == "session_deleted" or data.get("op") == "session_deleted":
                            deleted = True
                        else:
                            cited = True
        except OSError:
            return False
        if deleted:
            return False
        if cited:
            return not (self.sessions_dir / f"{session_id}.json").exists()
        return False

    def delete_session(self, session_id: str) -> None:
        """Delete a session file.  Clears the active pointer if deleted."""
        from snodo.infrastructure.state import read_state, write_state

        session = self.load_session(session_id)  # Validate exists
        if self._audit_log is not None:
            self._audit_log.append_event("session_deleted", {
                "op": "session_deleted",
                "session_id": session_id,
            })
        elif session.project_root:
            from snodo.infrastructure.audit import get_audit_log
            audit_path = Path(session.project_root) / ".snodo" / "audit.log"
            if audit_path.parent.is_dir():
                try:
                    alog = get_audit_log(str(audit_path), project_id=session.project_id)
                    alog.append_event("session_deleted", {
                        "op": "session_deleted",
                        "session_id": session_id,
                    })
                except Exception as exc:
                    _logger.debug("Could not record session_deleted event: %s", exc)
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
        if project_root:
            if (Path(project_root) / ".snodo").is_dir():
                pid = get_project_id(project_root)[0]
            else:
                from snodo.project import resolve_project_id
                pid = resolve_project_id(project_root)[0]
        else:
            pid = None
        results: List[SessionState] = []
        for session_file in sorted(self.sessions_dir.glob("*.json")):
            try:
                session = self._load_file(session_file)
            except _CORRUPT_SESSION_EXCEPTIONS as exc:
                self._warn_corrupt(session_file, exc)
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
            except _CORRUPT_SESSION_EXCEPTIONS as exc:
                self._warn_corrupt(session_file, exc)
                continue
            try:
                from snodo.infrastructure.state import read_state
                state = read_state(s.project_root)
                for sid in state.active_session.values():
                    active_ids.add(sid)
            except (OSError, PermissionError) as exc:
                _logger.warning(
                    "Could not read state for %s while pruning: %s",
                    s.project_root, exc,
                )

        for session_file in list(self.sessions_dir.glob("*.json")):
            try:
                session = self._load_file(session_file)
            except _CORRUPT_SESSION_EXCEPTIONS as exc:
                self._warn_corrupt(session_file, exc)
                continue

            if session.session_id in active_ids:
                continue

            def _audit_deleted(s: SessionState) -> None:
                if self._audit_log is not None:
                    self._audit_log.append_event("session_deleted", {
                        "op": "session_deleted",
                        "session_id": s.session_id,
                    })
                elif s.project_root:
                    from snodo.infrastructure.audit import get_audit_log
                    audit_path = Path(s.project_root) / ".snodo" / "audit.log"
                    if audit_path.parent.is_dir():
                        try:
                            alog = get_audit_log(str(audit_path), project_id=s.project_id)
                            alog.append_event("session_deleted", {
                                "op": "session_deleted",
                                "session_id": s.session_id,
                            })
                        except Exception as exc:
                            _logger.debug("Could not record session_deleted event: %s", exc)

            try:
                updated = datetime.fromisoformat(session.updated_at)
            except (ValueError, TypeError):
                _audit_deleted(session)
                session_file.unlink(missing_ok=True)
                pruned += 1
                continue

            if updated < cutoff:
                _audit_deleted(session)
                session_file.unlink(missing_ok=True)
                pruned += 1
        return pruned

    def _save_session(self, session: SessionState) -> None:
        """Atomically save session state to JSON file.

        Uses atomic_update_json (with flock and thread lock) writing to a unique
        temporary file before replacing onto the target, ensuring process and
        thread safety.
        """
        from snodo.infrastructure.state import atomic_update_json

        data = asdict(session)

        def _updater(existing: Dict[str, Any]) -> None:
            existing.clear()
            existing.update(data)

        atomic_update_json(
            self.sessions_dir, f"{session.session_id}.json", _updater, strict=True,
        )

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
