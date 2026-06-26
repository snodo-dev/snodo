"""Writeback node mixin.

FILE: snodo/engine/nodes/writeback.py
"""

import json
import os as _os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from snodo.engine.state import _task_branch_name


class WritebackMixin:
    """Mixin providing payload persistence and decision writeback capabilities."""

    def _auto_write_pending_decisions(self, loop_state: Any, results: list) -> None:
        """Write pending_decision entries for every blocking/escalating validator."""
        if not self._session_manager or not self._session_id:
            return

        task_id = loop_state.task.id
        try:
            session = self._session_manager.load_session(self._session_id)
        except Exception:
            return

        pending = session.checkpoint.decisions.get("pending_decisions", {})
        if not isinstance(pending, dict):
            pending = {}

        now = datetime.now(timezone.utc).isoformat()

        for r in results:
            if r.severity not in ("blocker", "warn"):
                continue
            entry = {
                "type": "adjudicate",
                "validator_id": r.validator_id,
                "decision": "proceed",
                "justification": r.justification,
                "severity": r.severity,
                "proposed_by": "engine",
                "timestamp": now,
            }
            pending[task_id] = entry

        self._session_manager.update_decision(
            self._session_id, "pending_decisions", pending,
        )

    def _auto_write_failure_context(self, loop_state: Any, results: list) -> None:
        """Persist structured failure context for retry when a task halts."""
        if not self._session_manager or not self._session_id:
            return

        task_id = loop_state.task.id
        try:
            session = self._session_manager.load_session(self._session_id)
        except Exception:
            return

        failures = session.checkpoint.decisions.get("task_failure", {})
        if not isinstance(failures, dict):
            failures = {}

        existing = failures.get(task_id, {}) if isinstance(failures.get(task_id), dict) else {}
        attempt = existing.get("attempt", 0) + 1

        branch_name = _task_branch_name(task_id, loop_state.task.spec)

        failures[task_id] = {
            "spec": loop_state.task.spec,
            "branch": branch_name,
            "attempt": attempt,
            "failed_validators": [
                {
                    "validator_id": r.validator_id,
                    "severity": r.severity,
                    "justification": r.justification,
                }
                for r in results
                if r.severity in ("blocker", "warn")
            ],
            "files_changed": list(loop_state.artifacts),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self._session_manager.update_decision(
            self._session_id, "task_failure", failures,
        )

    def _clear_failure_context(self, loop_state: Any) -> None:
        """Remove failure context for a task when execution succeeds."""
        if not self._session_manager or not self._session_id:
            return

        task_id = loop_state.task.id
        try:
            session = self._session_manager.load_session(self._session_id)
        except Exception:
            return

        failures = session.checkpoint.decisions.get("task_failure", {})
        if isinstance(failures, dict) and task_id in failures:
            del failures[task_id]
            try:
                self._session_manager.update_decision(
                    self._session_id, "task_failure", failures,
                )
            except Exception:
                pass

    def _merge_into_job_state(self, updates: dict) -> None:
        """Atomically merge *updates* into the job's state.json (direct write)."""
        if not self._job_id or not self._project_root:
            return
        job_dir = Path(self._project_root) / ".snodo" / "jobs" / self._job_id
        if not job_dir.is_dir():
            return
        state_path = job_dir / "state.json"
        state = {}
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
            except Exception:
                pass
        state.update(updates)
        tmp = job_dir / "state.json.tmp"
        tmp.write_text(json.dumps(state, indent=2))
        _os.replace(str(tmp), str(state_path))

    def _build_halt_payload(self, loop_state: Any) -> dict:
        """Construct the halt payload dict from the loop state."""
        meta = loop_state.metadata
        phase = "unknown"
        if loop_state.is_complete:
            phase = "complete"
        elif loop_state.is_blocked:
            phase = "pre_execute" if meta.get("post_validation") is None else "post_execute"
        if loop_state.halt_type == "escalated":
            phase = loop_state.pending_disagreement.get("phase", "unknown") if loop_state.pending_disagreement else "unknown"

        blocker_reason = "; ".join(loop_state.constraint_violations) if loop_state.constraint_violations else None

        return {
            "final_decision": "blocked" if loop_state.is_blocked else "completed",
            "phase": phase,
            "halt_type": loop_state.halt_type,
            "pre_validation": meta.get("pre_validation"),
            "post_validation": meta.get("post_validation"),
            "blocker_reason": blocker_reason,
            "artifacts_count": len(loop_state.artifacts),
        }

    def _auto_write_halt_payload(self, loop_state: Any) -> None:
        """Persist halt payload — dual-write: session checkpoint + job state.json."""
        halt_payload = self._build_halt_payload(loop_state)

        # Direct write to job state.json
        self._merge_into_job_state({"halt": halt_payload})

        # Dual-write to session for orchestrator / dashboard
        if not self._session_manager or not self._session_id:
            return
        task_id = loop_state.task.id
        try:
            session = self._session_manager.load_session(self._session_id)
        except Exception:
            return
        halt = session.checkpoint.decisions.get("halt", {})
        if not isinstance(halt, dict):
            halt = {}
        halt[task_id] = halt_payload
        self._session_manager.update_decision(
            self._session_id, "halt", halt,
        )

    def _auto_write_classification(self, loop_state: Any) -> None:
        """Persist flow_type / wave_id — dual-write: session + job state.json."""
        flow_type = loop_state.task.flow_type
        wave_id = loop_state.task.wave_id

        # Direct write to job state.json
        updates = {}
        if flow_type:
            updates["flow_type"] = flow_type
        if wave_id:
            updates["wave_id"] = wave_id
        if updates:
            self._merge_into_job_state(updates)

        # Dual-write to session
        if not self._session_manager or not self._session_id:
            return
        task_id = loop_state.task.id
        try:
            session = self._session_manager.load_session(self._session_id)
        except Exception:
            return
        classifications = session.checkpoint.decisions.get("classification", {})
        if not isinstance(classifications, dict):
            classifications = {}
        classifications[task_id] = {
            "flow_type": flow_type,
            "wave_id": wave_id,
            "task_spec": loop_state.task.spec[:200],
        }
        self._session_manager.update_decision(
            self._session_id, "classification", classifications,
        )

    def _find_verified_coder_override(self) -> Optional[dict]:
        """Find a verified set_model(scope=coder) override, if one exists."""
        if not self._authorized_decisions or not self._decision_issuer:
            return None

        verified = self._decision_issuer.find_set_model_overrides(
            self._authorized_decisions,
        )
        return next(
            (p for p in verified if p.get("scope") == "coder"), None
        )

    def _maybe_respawn_coder(self) -> None:
        """Respawn the coder if a verified set_model(scope=coder) override exists."""
        override = self._find_verified_coder_override()
        if override is None:
            return

        new_model = override.get("proposed_model", "")
        if not new_model or new_model == getattr(self.coder, "model", ""):
            return

        from snodo.coders import resolve_adapter_class
        from snodo.infrastructure.config import load_llm_config

        llm_cfg = load_llm_config()
        adapter_cls = resolve_adapter_class(new_model)
        fresh_coder = adapter_cls(
            model=new_model,
            max_tokens=llm_cfg.coder.max_tokens,
            max_tool_turns=llm_cfg.coder.max_tool_turns,
            workspace_mcp=self.workspace_mcp,
        )
        if hasattr(fresh_coder, "_job_id") and self._job_id:
            fresh_coder._job_id = self._job_id

        old_model = getattr(self.coder, "model", "")
        self.coder = fresh_coder
        self._completion_fn = getattr(fresh_coder, "_completion_fn", None) or \
                              getattr(fresh_coder, "completion_fn", None)
        self._default_model = new_model

        # Keep the validator runner in sync
        self._validator_runner._completion_fn = self._completion_fn
        self._validator_runner._default_model = self._default_model

        self._audit("coder_respawned", {
            "op": "coder_respawned",
            "old_model": old_model,
            "new_model": new_model,
        })
