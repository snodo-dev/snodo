"""UI-agnostic read-layer for the Snodo dashboard.

FILE: snodo/dashboard/providers.py

Aggregates existing managers into a workspace -> sessions -> agents/validators -> events tree.
Kept free of Textual imports so a future `snodo dashboard --web` can reuse it unchanged.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from snodo.compiler.models import Protocol, Mode
from snodo.infrastructure.audit import AuditLog, AuditEvent
from snodo.infrastructure.state import read_state


@dataclass
class SessionSummary:
    """Lightweight session row data for the sessions list view."""
    session_id: str
    mode: str
    created_at: str
    updated_at: str
    is_active: bool
    current_task: Optional[str] = None
    agent_count: int = 0
    validator_count: int = 0
    last_event_type: Optional[str] = None
    last_event_at: Optional[str] = None
    is_escalated: bool = False
    is_halted: bool = False


@dataclass
class SessionDetail:
    """Full session detail for the drill-down view."""
    session_id: str
    mode_id: str
    mode_name: str
    created_at: str
    updated_at: str
    current_task: Optional[str]
    memory_summary: str
    decisions: Dict[str, Any]
    validators: List[Dict[str, Any]] = field(default_factory=list)
    agents: List[Dict[str, Any]] = field(default_factory=list)
    events: List[AuditEvent] = field(default_factory=list)
    task_history: List[Dict[str, Any]] = field(default_factory=list)
    is_escalated: bool = False
    is_halted: bool = False


class DashboardDataProvider:
    """Aggregates session, protocol, agent, and audit data for the dashboard.

    No Textual imports.  All methods return plain Python types so the TUI
    and a future web layer can consume the same data unchanged.
    """

    def __init__(self, project_root: str):
        self.project_root = str(Path(project_root).resolve())
        self._project_name = Path(self.project_root).name
        self._audit_log: Optional[AuditLog] = None
        self._protocol: Optional[Protocol] = None
        self._protocol_error: Optional[str] = None

    @property
    def project_name(self) -> str:
        return self._project_name

    # ------------------------------------------------------------------
    # Protocol (lazy, cached)
    # ------------------------------------------------------------------

    def get_protocol(self) -> Optional[Protocol]:
        if self._protocol is not None:
            return self._protocol
        if self._protocol_error is not None:
            return None
        protocol_path = Path(self.project_root) / ".snodo" / "protocol.yml"
        if not protocol_path.exists():
            self._protocol_error = "No protocol.yml found"
            return None
        try:
            from snodo.protocols import load_protocol
            self._protocol = load_protocol(protocol_path)
        except Exception as e:
            self._protocol_error = str(e)
        return self._protocol

    def get_protocol_error(self) -> Optional[str]:
        self.get_protocol()  # trigger load
        return self._protocol_error

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def get_active_session_id(self, mode: str = "") -> Optional[str]:
        state = read_state(self.project_root)
        if mode:
            return state.active_session.get(mode)
        return state.active_session.get(self.get_active_mode())

    def get_active_mode(self) -> str:
        state = read_state(self.project_root)
        return state.current_mode or ""

    def get_sessions(self) -> List[SessionSummary]:
        """Return all sessions for the current project, active pinned first."""
        from snodo.infrastructure.session import SessionManager
        mgr = SessionManager()
        sessions = mgr.list_sessions(project_root=self.project_root)

        state = read_state(self.project_root)
        active_ids = set(state.active_session.values())
        audit = self._get_audit_log()

        summaries = []
        for s in sessions:
            is_active = s.session_id in active_ids
            mode_obj = self._get_mode(s.mode)

            agent_count = self._count_agents_for_mode(s.mode)
            validator_count = len(self._get_validator_ids(mode_obj))

            last_event_type = None
            last_event_at = s.updated_at
            is_escalated = False
            is_halted = False

            if audit:
                se = self._session_events(audit, s.session_id)
                if se:
                    last_event_type = se[-1].event_type
                    last_event_at = se[-1].timestamp
                    for ev in se:
                        if ev.event_type == "disagreement_escalated":
                            is_escalated = True
                        if ev.event_type == "halt":
                            is_halted = True

            summaries.append(SessionSummary(
                session_id=s.session_id,
                mode=s.mode,
                created_at=s.created_at,
                updated_at=s.updated_at,
                is_active=is_active,
                current_task=s.checkpoint.current_task,
                agent_count=agent_count,
                validator_count=validator_count,
                last_event_type=last_event_type,
                last_event_at=last_event_at,
                is_escalated=is_escalated,
                is_halted=is_halted,
            ))

        summaries.sort(key=lambda x: (not x.is_active, x.updated_at), reverse=False)
        return summaries

    def get_session_detail(self, session_id: str) -> Optional[SessionDetail]:
        """Return full detail for one session."""
        from snodo.infrastructure.session import SessionManager
        mgr = SessionManager()
        try:
            session = mgr.load_session(session_id)
        except FileNotFoundError:
            return None

        mode_obj = self._get_mode(session.mode)
        mode_name = mode_obj.name if mode_obj else session.mode

        validator_data = self._build_validator_data(mode_obj)
        agent_data = self._get_agents_for_mode(session.mode)
        audit = self._get_audit_log()
        events = self._session_events(audit, session_id) if audit else []

        is_escalated = any(e.event_type == "disagreement_escalated" for e in events)
        is_halted = any(e.event_type == "halt" for e in events)

        task_history = _build_task_history(events)

        return SessionDetail(
            session_id=session.session_id,
            mode_id=session.mode,
            mode_name=mode_name,
            created_at=session.created_at,
            updated_at=session.updated_at,
            current_task=session.checkpoint.current_task,
            memory_summary=session.checkpoint.memory_summary,
            decisions=session.checkpoint.decisions,
            validators=validator_data,
            agents=agent_data,
            events=events[-20:],
            task_history=task_history,
            is_escalated=is_escalated,
            is_halted=is_halted,
        )

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def get_all_events(self, limit: int = 100) -> List[AuditEvent]:
        audit = self._get_audit_log()
        if audit is None:
            return []
        return audit.get_history()[-limit:]

    # ------------------------------------------------------------------
    # Waves, Tasks, Jobs, and Logs (Cockpit support)
    # ------------------------------------------------------------------

    def get_waves(self, session_id: str) -> List[Dict[str, Any]]:
        wave_path = Path(self.project_root) / ".snodo" / "wave.json"
        if not wave_path.exists():
            return []
        try:
            import json
            with open(wave_path) as f:
                data = json.load(f)
            res = []
            for item in data:
                res.append({
                    "wave_id": item.get("wave_id"),
                    "feature_description": item.get("feature_description"),
                    "task_ids": item.get("task_ids", []),
                    "created": item.get("created"),
                    "last_activity": item.get("last_activity"),
                })
            return res
        except Exception:
            return []

    def get_tasks(self, session_id: str) -> List[Dict[str, Any]]:
        plans_dir = Path(self.project_root) / ".snodo" / "plans"
        if not plans_dir.exists():
            return []
        tasks = []
        try:
            import json
            for plan_path in plans_dir.iterdir():
                if plan_path.is_dir():
                    status_file = plan_path / "status.json"
                    if status_file.exists():
                        with open(status_file) as f:
                            data = json.load(f)
                        for tid, entry in data.get("tasks", {}).items():
                            status = entry if isinstance(entry, str) else entry.get("status", "unknown")
                            parent = None if isinstance(entry, str) else entry.get("parent_task_ref")
                            depth = 0 if isinstance(entry, str) else entry.get("depth", 0)
                            tasks.append({
                                "plan_name": plan_path.name,
                                "task_id": tid,
                                "task_ref": f"{plan_path.name}:{tid}",
                                "status": status,
                                "parent_task_ref": parent,
                                "depth": depth,
                            })
        except Exception:
            pass
        return tasks

    def get_jobs(self, session_id: str, task_ref: str) -> List[Dict[str, Any]]:
        jobs_dir = Path(self.project_root) / ".snodo" / "jobs"
        if not jobs_dir.exists():
            return []
        
        target_task_id = task_ref.split(":")[-1] if ":" in task_ref else task_ref
        
        import json
        import time
        jobs = []
        try:
            for job_path in jobs_dir.iterdir():
                if job_path.is_dir():
                    task_path = job_path / "task.json"
                    state_path = job_path / "state.json"
                    if task_path.exists() and state_path.exists():
                        with open(task_path) as f:
                            task_data = json.load(f)
                        
                        job_task_id = task_data.get("task_id", "")
                        job_retry_task_id = task_data.get("retry_task_id", "")
                        if job_task_id != target_task_id and job_retry_task_id != target_task_id:
                            continue
                            
                        with open(state_path) as f:
                            state_data = json.load(f)
                        
                        started = state_data.get("started_at")
                        completed = state_data.get("completed_at")
                        duration = 0.0
                        if started:
                            if completed:
                                duration = completed - started
                            else:
                                duration = time.time() - started
                        
                        jobs.append({
                            "job_id": job_path.name,
                            "status": state_data.get("status", "unknown"),
                            "duration": duration,
                            "created_at": state_data.get("created_at"),
                            "started_at": started,
                            "completed_at": completed,
                            "exit_code": state_data.get("exit_code"),
                        })
        except Exception:
            pass
            
        jobs.sort(key=lambda x: x.get("created_at") or 0.0)
        return jobs

    def get_job_log(self, session_id: str, task_ref: str, job_id: str) -> str:
        job_dir = Path(self.project_root) / ".snodo" / "jobs" / job_id
        if not job_dir.exists():
            return "No log found: job directory does not exist."
        
        log_content = []
        stdout_file = job_dir / "stdout.log"
        stderr_file = job_dir / "stderr.log"
        
        if stdout_file.exists():
            try:
                log_content.append(stdout_file.read_text(errors="replace"))
            except Exception as e:
                log_content.append(f"Error reading stdout: {e}")
        
        if stderr_file.exists():
            try:
                err_text = stderr_file.read_text(errors="replace")
                if err_text.strip():
                    log_content.append("\n--- STDERR ---\n" + err_text)
            except Exception as e:
                log_content.append(f"Error reading stderr: {e}")
                 
        return "".join(log_content) if log_content else "No log records found."

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_audit_log(self) -> Optional[AuditLog]:
        if self._audit_log is not None:
            return self._audit_log
        log_path = Path(self.project_root) / ".snodo" / "audit.log"
        if not log_path.exists():
            return None
        # Reset the global singleton so we always get a fresh load
        import snodo.infrastructure.audit as _audit_mod
        _audit_mod._global_audit_log = None  # type: ignore[attr-defined]
        from snodo.project import get_project_id
        project_id, _ = get_project_id(self.project_root)
        self._audit_log = AuditLog(str(log_path), project_id=project_id)
        return self._audit_log

    def _get_mode(self, mode_id: str) -> Optional[Mode]:
        protocol = self.get_protocol()
        if protocol is None:
            return None
        return protocol.get_mode(mode_id)

    def _get_validator_ids(self, mode_obj: Optional[Mode]) -> List[str]:
        if mode_obj is None:
            return []
        return list(mode_obj.validators)

    def _count_agents_for_mode(self, mode: str) -> int:
        try:
            from snodo.infrastructure.memory import AgentMemoryManager
            mgr = AgentMemoryManager()
            agents = mgr.list_agents()
            prefix = f"{self._project_name}:{mode}"
            return sum(1 for a in agents if a.get("id") == prefix)
        except Exception:
            return 0

    def _get_agents_for_mode(self, mode: str) -> List[Dict[str, Any]]:
        try:
            from snodo.infrastructure.memory import AgentMemoryManager
            mgr = AgentMemoryManager()
            agents = mgr.list_agents()
            prefix = f"{self._project_name}:{mode}"
            return [a for a in agents if a.get("id") == prefix]
        except Exception:
            return []

    def _build_validator_data(
        self, mode_obj: Optional[Mode]
    ) -> List[Dict[str, Any]]:
        if mode_obj is None:
            return []
        protocol = self.get_protocol()
        if protocol is None:
            return []
        result = []
        for vid in mode_obj.validators:
            v = protocol.get_validator(vid)
            if v is None:
                result.append({"validator_id": vid, "validator_type": "unknown"})
            else:
                result.append({
                    "validator_id": v.validator_id,
                    "validator_type": v.validator_type,
                    "evaluation_phase": v.evaluation_phase,
                    "severity_cap": v.severity_cap.value if v.severity_cap else None,  # type: ignore[dict-item]
                    "criteria": v.criteria,  # type: ignore[dict-item]
                })
        return result

    def _session_events(
        self, audit: AuditLog, session_id: str
    ) -> List[AuditEvent]:
        """Filter audit events that belong to a session.

        First pass: events directly tagged with session_id (post-fix engine events).
        Second pass: events correlated via task_ref from session_task_changed events
        (covers pre-fix events that lack session_id).
        """
        all_events = audit.get_history()
        result: List[AuditEvent] = []
        session_task_refs: set = set()

        # First pass: directly tagged events, and collect task_refs
        for ev in all_events:
            data = ev.data if isinstance(ev.data, dict) else {}
            if data.get("session_id") == session_id:
                result.append(ev)
                if ev.event_type in ("session_task_changed",):
                    new_task = data.get("new_task") or data.get("task_ref")
                    if new_task:
                        session_task_refs.add(new_task)
                    old_task = data.get("old_task") or data.get("previous_task_ref")
                    if old_task:
                        session_task_refs.add(old_task)

        # Second pass: correlate via task_ref for events without session_id
        if session_task_refs:
            for ev in all_events:
                data = ev.data if isinstance(ev.data, dict) else {}
                if data.get("session_id"):
                    continue  # already handled in first pass
                task_ref = data.get("task_ref", "")
                if task_ref and task_ref in session_task_refs:
                    result.append(ev)

        return result


def _build_task_history(events: list) -> list:
    """Build per-task summary rows from session_task_changed + correlated events.

    Preferred source: session_task_changed events give the ordered task list.
    Fallback: if none exist, collect task_refs from all events that carry one
    (governance_check, validate, dispatch, halt, task_complete), deduplicated
    and ordered by first-seen.
    """
    ordered_task_refs: list = []
    seen: set = set()
    by_task: dict[str, list] = {}
    has_session_changed = False

    for ev in events:
        data = ev.data if isinstance(ev.data, dict) else {}
        if ev.event_type == "session_task_changed":
            has_session_changed = True
            new_task = data.get("new_task", "")
            if new_task and new_task not in seen:
                ordered_task_refs.append(new_task)
                seen.add(new_task)
                if new_task not in by_task:
                    by_task[new_task] = []
        else:
            task_ref = data.get("task_ref", "")
            if task_ref:
                if task_ref not in by_task:
                    by_task[task_ref] = []

    # Fallback: no session_task_changed — collect task_refs from all events
    if not has_session_changed:
        ordered_task_refs = []
        seen.clear()
        for ev in events:
            data = ev.data if isinstance(ev.data, dict) else {}
            task_ref = data.get("task_ref", "")
            if task_ref and task_ref not in seen:
                ordered_task_refs.append(task_ref)
                seen.add(task_ref)

    # Correlate non-session_task_changed events to each task_ref
    for ev in events:
        data = ev.data if isinstance(ev.data, dict) else {}
        if ev.event_type == "session_task_changed":
            continue
        task_ref = data.get("task_ref", "")
        if task_ref and task_ref in by_task:
            by_task[task_ref].append(ev)

    rows = []
    for task_ref in ordered_task_refs:
        evs = by_task.get(task_ref, [])
        pre_results: list = []
        post_results: list = []
        outcome = "—"
        outcome_color = ""

        for e in evs:
            ed = e.data if isinstance(e.data, dict) else {}
            if e.event_type == "validate":
                phase = ed.get("phase", "")
                results = ed.get("results", [])
                if phase == "pre_execute":
                    pre_results = results
                elif phase == "post_execute":
                    post_results = results
            elif e.event_type == "halt":
                outcome = f"halted: {ed.get('reason', 'blocker')[:40]}"
                outcome_color = "red"
            elif e.event_type == "task_complete":
                outcome = "completed"
                outcome_color = "green"
            elif e.event_type == "dispatch":
                count = ed.get("artifacts_count", 0)
                outcome = f"dispatched ({count} files)"
                outcome_color = "green"

        rows.append({
            "task_ref": task_ref,
            "pre_results": pre_results,
            "post_results": post_results,
            "outcome": outcome,
            "outcome_color": outcome_color,
        })
    return rows
