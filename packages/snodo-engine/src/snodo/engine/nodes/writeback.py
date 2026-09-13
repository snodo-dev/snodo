"""Writeback node mixin.

FILE: snodo/engine/nodes/writeback.py
"""

import json
import logging
import os as _os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from snodo.engine.policy import policy_decision_to_dict
from snodo.engine.state import _task_branch_name
from snodo.core.interfaces import result_record
from snodo.core.spec import same_spec

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
# ``raw_halt_type``. The canonical vocabulary is five values — escalate,
# blocker, validator_error, internal_error, and ``environment_error``, the fifth,
# which is not a verdict about the task. ADR 015 is the home of the taxonomy and
# the reasoning for the fifth; this map only applies it.
_CANONICAL_HALT = {
    "escalated": "escalate",
    "blocked": "blocker",
    "validator_error": "validator_error",
    "internal_error": "internal_error",
    "constraint": "blocker",
    "wf3": "blocker",
    "max_iterations": "blocker",
    "turn_budget_exhausted": "blocker",
    # A coder fault the operator fixes in configuration (model string,
    # backend choice) stays a config-targeted blocker (#195); the missing-
    # program case is ``environment_error`` below.
    "execution_error": "blocker",
    # An environment fault is not a verdict, so its raw and canonical values
    # are the same (ADR 015).
    "environment_error": "environment_error",
    "recovery_exhausted": "blocker",
    "recovery_stalled": "blocker",
    # A post-execute judge that never reached a verdict, and the re-judging
    # bound (or a repeated abstention) is exhausted. Not a finding about the
    # code, so it is not "blocked" in the coder-recovery sense — but it is a
    # failure to reach consensus, which is a blocker-class halt (Fixes #268).
    "abstention_exhausted": "blocker",
    "abstention_stalled": "blocker",
    "head_not_moved": "blocker",
    "no_file_operations": "blocker",
}


def _canonical_halt(halt_type: Optional[str]) -> str:
    return _CANONICAL_HALT.get(halt_type or "", halt_type or "unknown")


# Canonical per-attempt outcomes. Unlike the prose in ``validator_results``,
# these tokens mean the same thing in every project, so a consumer can count a
# task's attempts and non-verdicts from payloads alone.
_ATTEMPT_PASSED = "passed"
_ATTEMPT_WARNED = "warned"
_ATTEMPT_BLOCKED = "blocked"
_ATTEMPT_ABSTAINED = "abstained"
_ATTEMPT_ERROR = "error"

# Raw halt types where the coder never ran or never returned work — an engine or
# environment fault, not a judge's verdict. The final attempt is an error rather
# than a pass-by-absence (Fixes #271).
_ATTEMPT_ERROR_HALTS = frozenset({
    "validator_error",
    "internal_error",
    "execution_error",
    "environment_error",
    "turn_budget_exhausted",
})

# The most attempts a halt payload enumerates. The counts stay exact however
# long the chain; only the list is capped, keeping the most recent entries, so a
# pathological run cannot grow the payload without bound. Recovery is already
# bounded by the protocol, but the payload owns its own guard.
_MAX_ATTEMPT_HISTORY = 20


def _verdict_outcome(results: Optional[List[Any]]) -> str:
    """Reduce one attempt's verdicts to a single canonical outcome.

    Ordering is deliberate: an engine error outranks a finding, a blocker
    outranks a warning, a warning outranks an abstention, and a pass is only
    reported when nothing else was observed. A pass alongside an abstention
    still counts as passed — a verdict was reached and carried the attempt; the
    abstention belongs to the retry history, not to this attempt's outcome.
    """
    has_error = has_blocker = has_warn = has_abstain = has_pass = False
    for r in results or []:
        if isinstance(r, dict):
            severity = r.get("severity")
            error = bool(r.get("error", False))
        else:
            severity = getattr(r, "severity", None)
            error = bool(getattr(r, "error", False))
        if error:
            has_error = True
        elif severity == "blocker":
            has_blocker = True
        elif severity == "warn":
            has_warn = True
        elif severity == "pass":
            has_pass = True
        elif severity is None:
            has_abstain = True
    if has_error:
        return _ATTEMPT_ERROR
    if has_blocker:
        return _ATTEMPT_BLOCKED
    if has_warn:
        return _ATTEMPT_WARNED
    if has_abstain and not has_pass:
        return _ATTEMPT_ABSTAINED
    return _ATTEMPT_PASSED


def _attempt_verdicts(loop_state: Any) -> Optional[List[Any]]:
    """The verdicts that concluded the attempt in hand.

    The post-execute run is the one that judges the produced work, so it is
    preferred; a task blocked before execution falls back to its pre-execute
    verdicts. The values may be records (dicts, as persisted in metadata) or
    result objects.
    """
    meta = getattr(loop_state, "metadata", {}) or {}
    for key in ("post_validation", "pre_validation"):
        phase = meta.get(key)
        if not isinstance(phase, dict):
            continue
        results = phase.get("validator_results")
        if isinstance(results, list) and results:
            return results
    return getattr(loop_state, "validation_results", None)


def _build_attempt_summary(loop_state: Any, phase: str) -> dict:
    """Summarise every attempt the task took, from the accumulated history.

    The final validator results say what happened last; this says how the task
    got there. Two attempts are invisible in the results and are recovered
    here:

    - the recovery subtasks that preceded this one, carried on the task as
      ``prior_failures`` tagged with their 1-based attempt number (abstentions
      included); and
    - the in-place abstention re-judges of the task in hand, counted by
      ``abstention_retries``, each of which re-ran a judge on unchanged work
      and dispatched no coder (Fixes #268).

    An attempt is one judged pass over a code state. The root is attempt 1, each
    recovery subtask is the next attempt, and each in-place re-judge is an
    attempt of its own. ``total`` and ``non_verdicts`` are exact at any length;
    ``history`` is capped at ``_MAX_ATTEMPT_HISTORY`` most-recent entries with
    the omitted count reported.
    """
    depth = getattr(loop_state.task, "depth", 0) or 0
    prior = getattr(loop_state.task, "prior_failures", None) or []

    by_attempt: Dict[Any, List[Any]] = {}
    for failure in prior:
        if isinstance(failure, dict):
            by_attempt.setdefault(failure.get("attempt"), []).append(failure)

    history: List[dict] = []

    # Attempts 1..depth are the recovery subtasks that preceded this one; each
    # produced the failures (abstentions included) that spawned the next.
    for number in range(1, depth + 1):
        history.append({
            "attempt": number,
            "outcome": _verdict_outcome(by_attempt.get(number, [])),
        })

    # Every in-place re-judge was an all-abstention pass on unchanged work; it
    # dispatched no coder and created no subtask.
    for _ in range(max(0, getattr(loop_state, "abstention_retries", 0) or 0)):
        history.append({
            "attempt": len(history) + 1,
            "outcome": _ATTEMPT_ABSTAINED,
        })

    if getattr(loop_state, "halt_type", None) in _ATTEMPT_ERROR_HALTS:
        final_outcome = _ATTEMPT_ERROR
    else:
        final_outcome = _verdict_outcome(_attempt_verdicts(loop_state))
    history.append({"attempt": len(history) + 1, "outcome": final_outcome})

    non_verdicts = sum(
        1 for entry in history if entry["outcome"] == _ATTEMPT_ABSTAINED
    )
    summary: Dict[str, Any] = {
        "total": len(history),
        "non_verdicts": non_verdicts,
        "coder_dispatches": depth + (0 if phase == "pre_execute" else 1),
        "history": history,
    }
    if len(history) > _MAX_ATTEMPT_HISTORY:
        summary["omitted"] = len(history) - _MAX_ATTEMPT_HISTORY
        summary["history"] = history[-_MAX_ATTEMPT_HISTORY:]
    return summary


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
    "config": (
        "Fix the coder configuration — the model string, the coder backend "
        "(--coder), or the tool's install — then re-run"
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
    - a loop that never converged (``max_iterations``, ``recovery_*``,
      ``turn_budget_exhausted``) is a spec or policy problem;
    - a post-execute rejection of produced artifacts is a code problem;
    - a pre-execute rejection of the proposal is a spec problem — unless the
      block cites a criterion, in which case the criterion lives in the
      protocol and is a legitimate place to fix it.
    """
    halt_type = halt_type or ""
    if halt_type in ("constraint", "wf3"):
        return ["policy"]
    if halt_type == "execution_error":
        # The coder backend itself failed — a binary missing from PATH, a CLI
        # that rejected the arguments (e.g. a model string the tool does not
        # accept), an LLM call that errored. The operator fixes the coder
        # configuration, not the code, the spec, or the protocol (Fixes #195).
        return ["config"]
    if halt_type in ("max_iterations", "turn_budget_exhausted", "recovery_exhausted", "recovery_stalled"):
        return ["spec", "policy"]
    if halt_type in ("abstention_exhausted", "abstention_stalled"):
        # The code was never faulted — a judge could not decide. The fix target
        # is the judge and its policy: the criteria or tool grant it needs, its
        # budget, or the abstention_policy the operator chose. Not the code,
        # which no verdict named (Fixes #268).
        return ["policy"]
    if halt_type == "head_not_moved":
        # The coder claimed a commit it did not make — the produced code is
        # what must change (the adapter must actually commit), so this is a
        # code fix, not a spec or policy problem.
        return ["code"]
    if halt_type == "no_file_operations":
        # The coder completed successfully (exit 0) but produced no file
        # operations. There is no produced code to fix; the fix targets are
        # the task spec and the coder backend or its configuration (Fixes #203).
        return ["spec", "config"]
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
    reason: Optional[str] = None,
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
    if halt == "environment_error":
        # The reason carries the coder's own message, which includes the
        # install command the adapter declares — the operator must see THAT
        # here, not a fix hint about a spec that passed every validator.
        detail = reason or (
            "the program the coder needs could not be invoked in the "
            "execution environment"
        )
        return (
            "This halt is about the execution environment, not about the "
            f"task: {detail} Nothing about the spec, the code or the "
            "protocol needs fixing, and no recovery attempt is warranted: "
            "install the program where the run executes, then re-run the "
            "task unchanged."
        )
    if halt_type in ("abstention_exhausted", "abstention_stalled"):
        # The code was never faulted: a judge could not reach a verdict and the
        # bounded re-judging is done. Re-running the coder cannot help — no
        # verdict named anything for it to change — so the operator is sent to
        # adjudicate the missing verdict, or to change the judge or its policy.
        stalled = halt_type == "abstention_stalled"
        lead = (
            "A judge abstained again on unchanged work"
            if stalled
            else "A judge could not reach a verdict within its retry budget"
        )
        return (
            f"{lead}. Nothing about the code was found at fault, so no coder "
            "recovery is warranted. Use `snodo authorize <task_id>` to adjudicate "
            "the missing verdict, or change the judge or its policy in "
            ".snodo/protocol.yml — a criterion, a tool grant, its budget, or "
            "`abstention_policy`."
        )
    if halt == "blocker":
        return _build_blocker_hint(halt_type, phase, results)
    return ""


def _coder_registry_name(coder: Any) -> str:
    """Return the registry name the operator would pass to ``--coder``.

    The coder instance may carry a ``coder_name`` attribute (set by the
    resolver) or be a registered class; fall back to the class name only when
    neither is available. The registry name, not the class name, is what the
    operator selects with ``--coder`` (Fixes #148).
    """
    name = getattr(coder, "coder_name", None)
    if name:
        return name
    from snodo.coders import CODER_REGISTRY
    cls = type(coder)
    for reg_name, reg_cls in CODER_REGISTRY.items():
        if reg_cls is cls:
            return reg_name
    return cls.__name__


class WritebackMixin:
    """Mixin providing payload persistence and decision writeback capabilities."""

    def _auto_write_pending_decisions(self, loop_state: Any, results: list) -> None:
        """Write pending_decision entries for every blocking/escalating validator.

        Abstentions (severity=None) are exactly the case a human must be able
        to adjudicate: the entry carries that a judge abstained, which judge,
        why it ran out, and what it did and did not examine — so `snodo
        authorize` renders something actionable instead of showing nothing
        about the silent judge (Fixes #252).
        """
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
            if r.severity not in ("blocker", "warn") and r.severity is not None:
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
            if r.severity is None:
                entry["abstention_reason"] = (
                    getattr(r, "abstention_reason", None)
                    or "judge did not reach a verdict"
                )
                if getattr(r, "examined", None):
                    entry["examined"] = list(r.examined)
                if getattr(r, "unexamined_tools", None):
                    entry["unexamined_tools"] = list(r.unexamined_tools)
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

        # Preserve the root/original spec across retries so the retry prompt and
        # task metadata never wrap or accumulate previous prompt wrappers.
        # Check task.root_spec first, then existing failure context, then loop_state.
        raw_spec = loop_state.task.spec
        original_spec = (
            getattr(loop_state.task, "root_spec", None)
            or existing.get("original_spec")
            or existing.get("spec")
            or raw_spec
        )

        branch_name = _task_branch_name(task_id, original_spec)

        # Derive failure phase
        meta = getattr(loop_state, "metadata", {}) or {}
        pv = meta.get("post_validation")
        if pv is None:
            phase = "pre_execute"
        elif isinstance(pv, dict) and pv.get("outcome") == "skipped":
            phase = "execute"
        else:
            phase = "post_execute"
        if getattr(loop_state, "halt_type", None) == "escalated":
            phase = loop_state.pending_disagreement.get("phase", phase) if loop_state.pending_disagreement else phase

        failed_validators = [
            {
                "validator_id": r.validator_id,
                "severity": r.severity,
                "justification": (
                    f"[judge could not decide: {r.abstention_reason or 'no verdict within budget'}] "
                    + r.justification
                ) if r.severity is None else r.justification,
            }
            for r in (results or [])
            if hasattr(r, "severity") and (r.severity in ("blocker", "warn") or r.severity is None)
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
            "spec": original_spec,
            "original_spec": original_spec,
            "branch": branch_name,
            "attempt": attempt,
            "phase": phase,
            "failed_validators": failed_validators,
            "files_changed": list(loop_state.artifacts),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        # A replaced spec must stay recoverable: this record is rewritten every
        # attempt and would otherwise drop the spec the previous attempt carried
        # the moment a retry replaces it. Anything superseded earlier is
        # carried forward, and a spec that has just been displaced by a new
        # authoritative one joins the history here too (the CLI records the
        # same event when it replaces a spec, so this is the backstop for any
        # path that reached the engine with a changed spec and no CLI).
        superseded = existing.get("superseded_specs")
        superseded = list(superseded) if isinstance(superseded, list) else []
        displaced = existing.get("original_spec") or existing.get("spec")
        if (
            isinstance(displaced, str) and displaced
            and not same_spec(displaced, original_spec)
            and displaced not in superseded
        ):
            superseded.append(displaced)
        if superseded:
            failures[task_id]["superseded_specs"] = superseded
            failures[task_id]["superseded_spec"] = superseded[-1]
        elif isinstance(existing.get("superseded_spec"), str):
            failures[task_id]["superseded_spec"] = existing["superseded_spec"]

        self._session_manager.update_decision(
            self._session_id, "task_failure", failures,
        )

    def _clear_failure_context(self, loop_state: Any) -> None:
        """Remove failure context and pending decisions for a task when it
        completes.

        A completed task must not leave an open adjudication request behind: a
        pending decision recorded by an earlier failed attempt is a standing
        proposal to proceed past a blocker that no longer exists (Fixes #148).
        """
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

        pending = session.checkpoint.decisions.get("pending_decisions", {})
        if isinstance(pending, dict) and task_id in pending:
            del pending[task_id]
            try:
                self._session_manager.update_decision(
                    self._session_id, "pending_decisions", pending,
                )
            except Exception as e:
                _logger.warning("Failed to update pending_decisions for session %s: %s", self._session_id, e)

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
        outcome vocabulary; see ADR 015). The engine's specific reason (e.g.
        which constraint, or which validator) is preserved in ``reason`` /
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

        coder_obj = getattr(self, "coder", None)
        coder_name = _coder_registry_name(coder_obj)
        if hasattr(coder_obj, "_bare_model"):
            bare = coder_obj._bare_model()
            coder_model = bare if bare else None
        else:
            coder_model = getattr(coder_obj, "model", None)

        judging_model = getattr(self, "_default_model", None)

        # Prefer original / root spec for task_spec in the halt payload
        authoritative_spec = getattr(loop_state.task, "root_spec", None) or loop_state.task.spec

        payload = {
            "status": "blocked" if loop_state.is_blocked else "completed",
            "halt_type": halt,
            "final_decision": halt,
            "raw_halt_type": halt,
            "reason": blocker_reason,
            "task_id": loop_state.task.id,
            "task_spec": authoritative_spec,
            "iteration": loop_state.iteration,
            "current_mode": loop_state.current_mode,
            "phase": phase,
            "coder": coder_name,
            "coder_model": coder_model,
            "judging_model": judging_model,
            # record(): the halt payload is what the orchestrator reads to
            # learn what happened; an abstention must not read as a pass
            # (Fixes #252).
            "validator_results": [
                result_record(r) for r in loop_state.validation_results
            ],
            # The history that precedes the results above: every attempt the
            # task took, canonical-outcome encoded so a clean pass and a
            # hard-won one are distinguishable without reading the logs
            # (Fixes #271).
            "attempts": _build_attempt_summary(loop_state, phase),
            "policy_decision": policy_decision_to_dict(loop_state.policy_decision),
            "hint": _build_hint(
                halt, loop_state.halt_type, phase,
                loop_state.validation_results, reason=blocker_reason,
            ),
            "pre_validation": meta.get("pre_validation"),
            "post_validation": meta.get("post_validation"),
            "spec_authoring": meta.get("spec_authoring"),
            "blocker_reason": blocker_reason,
            "artifacts_count": len(loop_state.artifacts),
        }
        if meta.get("timed_out") or getattr(self, "_last_timed_out", False):
            payload["timed_out"] = True
            payload["timeout_seconds"] = meta.get("timeout_seconds") or getattr(self, "_last_timeout_seconds", None)
        if commit_reason is not None:
            payload["commit_reason"] = commit_reason
        output_tail = meta.get("output_tail") or getattr(self, "_last_output_tail", "") or getattr(self, "_last_timeout_tail", "")
        if output_tail:
            payload["output_tail"] = output_tail
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
        # The session checkpoint is transmitted over the wire (emits session_decision_updated).
        # Strip output_tail so raw coder output never enters the audit log.
        wire_payload = dict(halt_payload)
        wire_payload.pop("output_tail", None)
        halt[task_id] = wire_payload
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
            timeout_seconds=llm_cfg.coder.timeout_seconds,
            workspace_mcp=self.workspace_mcp,
        )
        if hasattr(fresh_coder, "_job_id") and self._job_id:
            fresh_coder._job_id = self._job_id

        old_model = getattr(self.coder, "model", "")
        self.coder = fresh_coder
        self._completion_fn = getattr(fresh_coder, "_completion_fn", None) or \
                              getattr(fresh_coder, "completion_fn", None)
        self._default_model = new_model

        # Keep the validator runner completion function in sync
        if self._completion_fn is not None:
            self._validator_runner._completion_fn = self._completion_fn

        self._audit("coder_respawned", {
            "op": "coder_respawned",
            "old_model": old_model,
            "new_model": new_model,
        })
