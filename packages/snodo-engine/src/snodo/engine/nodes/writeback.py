"""Writeback node mixin.

FILE: snodo/engine/nodes/writeback.py
"""

import json
import logging
import os as _os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional
from snodo.engine.policy import policy_decision_to_dict
from snodo.engine.state import _task_branch_name

_logger = logging.getLogger(__name__)


class JobStateError(Exception):
    """The job's state.json is corrupt and could not be merged.

    Raised (after preserving the corrupt file and auditing the fault) instead
    of silently discarding the job's own record. Callers that must tolerate a
    corrupt job state have to catch this explicitly — the default is refusal.
    """


# Canonical halt outcome. The halt payload's ``halt_type`` and ``final_decision``
# are the SAME canonical value, so the payload is self-consistent (final_decision
# always equals halt_type). The engine's specific halt_type is preserved in
# ``raw_halt_type``; the coarse outcome is one of the four-status vocabulary
# (escalate / blocker / validator_error / internal_error) plus "completed".
_CANONICAL_HALT = {
    "escalated": "escalate",
    "blocked": "blocker",
    "validator_error": "validator_error",
    "internal_error": "internal_error",
    "constraint": "blocker",
    "wf3": "blocker",
    "max_iterations": "blocker",
    "execution_error": "internal_error",
    "recovery_exhausted": "blocker",
    "recovery_stalled": "blocker",
    "head_not_moved": "blocker",
}


def _canonical_halt(halt_type: Optional[str]) -> str:
    return _CANONICAL_HALT.get(halt_type or "", halt_type or "unknown")


# The three fix targets a blocker can have.  The hint names only the ones that
# apply to the halt in hand (Fixes #38); a hint that lists all three every time
# is no better than one that names one.
_BLOCKER_FIX_TARGETS = {
    "code": (
        "Fix the produced code and re-run"
    ),
    "spec": (
        "Revise the task spec so it states what you actually want, then re-run"
    ),
    "policy": (
        "Edit the protocol — .snodo/protocol.yml is a legitimate place to "
        "change a criterion or a tool grant"
    ),
}


def _blocker_fix_targets(
    halt_type: Optional[str],
    phase: str,
    results: Optional[List[Any]],
) -> List[str]:
    """Return the fix targets that apply to this halt, derived from the halt.

    A blocker has three fix targets — the code, the spec, or the policy. Which
    apply depends on the halt in hand:
    - a protocol violation (``constraint``, ``wf3``) is a policy problem;
    - a loop that never converged (``max_iterations``, ``recovery_*``) is a
      spec or policy problem;
    - a post-execute rejection of produced artifacts is a code problem;
    - a pre-execute rejection of the proposal is a spec problem — unless the
      block cites a criterion, in which case the criterion lives in the
      protocol and is a legitimate place to fix it.
    """
    halt_type = halt_type or ""
    if halt_type in ("constraint", "wf3"):
        return ["policy"]
    if halt_type in ("max_iterations", "recovery_exhausted", "recovery_stalled"):
        return ["spec", "policy"]
    if halt_type == "head_not_moved":
        # The coder claimed a commit it did not make — the produced code is
        # what must change (the adapter must actually commit), so this is a
        # code fix, not a spec or policy problem.
        return ["code"]
    if phase == "post_execute":
        return ["code"]
    if any(getattr(r, "cited_criteria", None) for r in (results or [])):
        return ["policy"]
    return ["spec"]


def _cited_criterion(results: Optional[List[Any]]) -> Optional[str]:
    """Return the first cited criterion from the blocking results, if any."""
    for r in (results or []):
        criteria = getattr(r, "cited_criteria", None) or []
        if criteria:
            return criteria[0]
    return None


def _build_blocker_hint(
    halt_type: Optional[str],
    phase: str,
    results: Optional[List[Any]],
) -> str:
    """Build a hint naming only the fix targets that apply to this blocker."""
    criterion = _cited_criterion(results)
    if criterion:
        return (
            "This block is based on a criterion. The criterion reads: "
            f"{criterion}. .snodo/protocol.yml is a legitimate place to fix "
            "it — the criterion may be stale or a tool grant may be missing; "
            "edit it and re-run."
        )

    targets = _blocker_fix_targets(halt_type, phase, results)
    phrases = [_BLOCKER_FIX_TARGETS[t] for t in targets]
    if not phrases:
        return "Address the blocking concerns and re-run a revised task."
    return "This halt can be fixed: " + " or ".join(phrases) + "."


def _build_hint(
    halt: str,
    halt_type: Optional[str] = "",
    phase: str = "",
    results: Optional[List[Any]] = None,
) -> str:
    if halt == "escalate":
        return (
            "Address the blocking concerns and re-run a revised task. "
            "If you believe the block is incorrect, use "
            "`snodo authorize <task_id>`.\n"
            "Run: snodo authorize to list all pending decisions."
        )
    if halt in ("validator_error", "internal_error"):
        return (
            "A validator or the engine failed internally (not an authorisation "
            "problem). Retry the task or inspect the logs."
        )
    if halt == "blocker":
        return _build_blocker_hint(halt_type, phase, results)
    return ""


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

        failed_validators = [
            {
                "validator_id": r.validator_id,
                "severity": r.severity,
                "justification": r.justification,
            }
            for r in (results or [])
            if hasattr(r, "severity") and r.severity in ("blocker", "warn")
        ]

        if not failed_validators and loop_state.constraint_violations:
            failed_validators = [
                {
                    "validator_id": loop_state.halt_type or "execution_error",
                    "severity": "blocker",
                    "justification": "; ".join(loop_state.constraint_violations),
                }
            ]

        failures[task_id] = {
            "spec": loop_state.task.spec,
            "branch": branch_name,
            "attempt": attempt,
            "failed_validators": failed_validators,
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
            except Exception as e:
                _logger.warning("Failed to update task_failure decision for session %s: %s", self._session_id, e)

    def _quarantine_corrupt_state(self, job_dir: Path, state_path: Path, error: str) -> Path:
        """Preserve a corrupt state.json under a .corrupt-<timestamp> name.

        Records the fault in the audit log and returns the quarantine path.
        The original bytes survive untouched; the caller will not write over
        the corrupt file.
        """
        corrupt_path = job_dir / (
            "state.json.corrupt-"
            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
        )
        _os.replace(str(state_path), str(corrupt_path))
        _logger.error(
            "Job state %s is corrupt (%s); preserved as %s and refusing to write",
            state_path, error, corrupt_path,
        )
        self._audit("job_state_corrupt", {
            "op": "job_state_corrupt",
            "job_id": self._job_id,
            "state_file": str(state_path),
            "preserved_as": str(corrupt_path),
            "error": error,
        })
        return corrupt_path

    def _merge_into_job_state(self, updates: dict) -> None:
        """Atomically merge *updates* into the job's state.json (direct write).

        state.json is the job's own record. A corrupt read is never resolved
        by overwriting it: the corrupt file is preserved under a
        ``state.json.corrupt-<timestamp>`` name, the fault is recorded in the
        audit log, and ``JobStateError`` is raised so the caller learns the
        merge did not happen. Callers that must tolerate a corrupt job state
        have to catch it explicitly — the default is refusal.
        """
        if not self._job_id or not self._project_root:
            return
        job_dir = Path(self._project_root) / ".snodo" / "jobs" / self._job_id
        if not job_dir.is_dir():
            return
        state_path = job_dir / "state.json"
        state: dict = {}
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
            except Exception as exc:
                corrupt_path = self._quarantine_corrupt_state(
                    job_dir, state_path, str(exc),
                )
                raise JobStateError(
                    f"Job state {state_path} is corrupt and could not be "
                    f"merged; preserved as {corrupt_path} (see audit log "
                    f"event job_state_corrupt)"
                ) from exc
            if not isinstance(state, dict):
                corrupt_path = self._quarantine_corrupt_state(
                    job_dir, state_path, "not a JSON object",
                )
                raise JobStateError(
                    f"Job state {state_path} is not a JSON object and could "
                    f"not be merged; preserved as {corrupt_path} (see audit "
                    f"log event job_state_corrupt)"
                )
        state.update(updates)
        tmp = job_dir / "state.json.tmp"
        tmp.write_text(json.dumps(state, indent=2))
        _os.replace(str(tmp), str(state_path))

    def _build_halt_payload(self, loop_state: Any) -> dict:
        """Construct the structured halt payload from the loop state.

        This is the SINGLE authoritative halt payload, emitted by the CLI and
        persisted to job state / session.  ``final_decision`` always equals
        ``halt_type``, which always equals ``raw_halt_type`` (canonical
        four-status vocabulary). The engine's specific reason (e.g. which
        constraint, or which validator) is preserved in ``reason`` /
        ``constraint_violations``, never silently remapped to another member of
        the vocabulary.
        """
        meta = loop_state.metadata
        phase = "unknown"
        if loop_state.is_complete:
            phase = "complete"
        elif loop_state.is_blocked:
            pv = meta.get("post_validation")
            if pv is None:
                phase = "pre_execute"
            elif isinstance(pv, dict) and pv.get("outcome") == "skipped":
                phase = "execute"
            else:
                phase = "post_execute"
        if loop_state.halt_type == "escalated":
            phase = loop_state.pending_disagreement.get("phase", "unknown") if loop_state.pending_disagreement else "unknown"

        blocker_reason = "; ".join(loop_state.constraint_violations) if loop_state.constraint_violations else None

        pv = meta.get("post_validation")
        commit_reason = pv.get("commit_reason") if isinstance(pv, dict) else None
        halt = _canonical_halt(loop_state.halt_type) if loop_state.is_blocked else "completed"

        payload = {
            "status": "blocked" if loop_state.is_blocked else "completed",
            "halt_type": halt,
            "final_decision": halt,
            "raw_halt_type": halt,
            "reason": blocker_reason,
            "task_id": loop_state.task.id,
            "task_spec": loop_state.task.spec,
            "iteration": loop_state.iteration,
            "current_mode": loop_state.current_mode,
            "phase": phase,
            "validator_results": [
                {"validator_id": r.validator_id, "severity": r.severity,
                 "justification": r.justification}
                for r in loop_state.validation_results
            ],
            "policy_decision": policy_decision_to_dict(loop_state.policy_decision),
            "hint": _build_hint(halt, loop_state.halt_type, phase, loop_state.validation_results),
            "pre_validation": meta.get("pre_validation"),
            "post_validation": meta.get("post_validation"),
            "spec_authoring": meta.get("spec_authoring"),
            "blocker_reason": blocker_reason,
            "artifacts_count": len(loop_state.artifacts),
        }
        if commit_reason is not None:
            payload["commit_reason"] = commit_reason
        return payload

    def _auto_write_halt_payload(self, loop_state: Any) -> None:
        """Persist halt payload — dual-write: session checkpoint + job state.json.

        Also attaches the payload to ``loop_state.metadata["halt_payload"]`` so
        it flows through the graph state to the closure driver and the CLI (single
        source of truth — the CLI does not re-derive it).
        """
        halt_payload = self._build_halt_payload(loop_state)

        # Attach to state so the closure driver / CLI can emit it.
        loop_state.metadata["halt_payload"] = halt_payload

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
        if self._completion_fn is not None:
            self._validator_runner._completion_fn = self._completion_fn
        self._validator_runner._default_model = self._default_model

        self._audit("coder_respawned", {
            "op": "coder_respawned",
            "old_model": old_model,
            "new_model": new_model,
        })
