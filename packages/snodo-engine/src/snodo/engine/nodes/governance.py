"""Governance node mixin.

FILE: snodo/engine/nodes/governance.py
"""

import logging
from typing import Any, Dict, List

from snodo.engine.state import LoopStage, LoopState

_logger = logging.getLogger(__name__)


class GovernanceNodeMixin:
    """Mixin providing governance node capabilities to GraphBuilder."""

    def _spec_authoring_reentry(self, loop_state: LoopState) -> LoopState:
        """Author an improved spec from the INTENT + spec-quality critique.

        Called when pre-execute validation escalated on warn-only spec
        validators.  One LLM call translates the raw intent into a proper
        spec (restated outcome, acceptance criteria, scope, intent +
        constraints).  Bounded to 2 attempts.

        Only spec-quality critique reaches the author.  A non-spec objection
        (architecture, security, ...) is about the work, not the wording;
        laundering it into the spec changes what the task wants, which is a
        redefinition, not a rewrite (Fixes #35).  The filter is applied here as
        well as at the escalation site, so a stale non-spec entry can never
        reach the author.
        """
        loop_state.spec_authoring_attempts += 1
        critique = loop_state.metadata.get("spec_critique", [])

        # Build spec-authoring prompt
        intent = loop_state.task.spec
        spec_critique = [c for c in critique if self._judges_spec(c.get("validator_id", ""))]
        critique_text = "\n".join(
            f"- [{c.get('validator_id', '?')}] {c.get('justification', '')}"
            for c in spec_critique
        )
        authoring_prompt = (
            "You are a spec author.  The following is a raw INTENT (e.g. a bug report).  "
            "A spec validator gave this critique:\n\n"
            f"{critique_text}\n\n"
            "Rewrite the intent into a well-formed spec that:\n"
            "1. Restates the desired outcome in 1-2 sentences (not a copy of the raw input)\n"
            "2. States explicit acceptance criteria (how we know it's resolved)\n"
            "3. States scope (which area/behaviour the change touches)\n"
            "4. Is intent + constraints — not transcribed implementation\n\n"
            "Return ONLY the authored spec text, nothing else.\n\n"
            f"INTENT:\n{intent}"
        )

        # Call the LLM via the classifier completion fn (same model path)
        try:
            fn = getattr(self, '_classifier_completion_fn', self._completion_fn)
            response = fn(
                messages=[{"role": "user", "content": authoring_prompt}],
            )
            authored_spec = response.choices[0].message.content.strip()
            if not authored_spec:
                authored_spec = intent  # fallback: keep original
        except Exception as exc:
            authored_spec = intent
            self._audit("spec_authored_failed", {
                "op": "spec_authored_failed",
                "task_ref": loop_state.task.id,
                "attempt": loop_state.spec_authoring_attempts,
                "error": str(exc),
            })

        before = loop_state.task.spec
        loop_state.task.spec = authored_spec
        loop_state.needs_spec_authoring = False
        loop_state.validation_results = []
        loop_state.validation_token = None
        loop_state.messages.append({
            "role": "assistant",
            "content": f"Authored spec (attempt {loop_state.spec_authoring_attempts}): {authored_spec[:300]}",
        })

        # Surface the rewrite where it happens: the spec the validators will now
        # run against is not what the operator typed.  Print the authored text
        # and the triggering critique at this point so a watcher sees the
        # replacement live, not only in the halt payload afterwards.
        progress = getattr(self, "_progress", None)
        if progress is not None:
            triggered_by = ", ".join(
                c.get("validator_id", "?") for c in spec_critique
            )
            progress(
                f"  Spec authored (attempt {loop_state.spec_authoring_attempts}), "
                f"triggered by {triggered_by}"
            )
            progress(f"    Original: {before[:300]}")
            progress(f"    Authored: {authored_spec[:300]}")
            progress(f"    Critique: {critique_text[:300]}")

        # Provenance: what triggered this, which attempt it was, and what the
        # original said.  Carried in metadata so the halt payload shows the
        # spec's origin rather than an invisible rewrite.
        loop_state.metadata["spec_authoring"] = {
            "attempt": loop_state.spec_authoring_attempts,
            "triggered_by": [c.get("validator_id") for c in spec_critique],
            "original": before,
            "authored": authored_spec,
        }

        self._audit("spec_authored", {
            "op": "spec_authored",
            "task_ref": loop_state.task.id,
            "attempt": loop_state.spec_authoring_attempts,
            "triggered_by": [c.get("validator_id") for c in spec_critique],
            "intent_preview": before[:400],
            "authored_spec_preview": authored_spec[:400],
            "critique": spec_critique,
        })

        return loop_state

    def _load_decision_records(self, loop_state: LoopState) -> None:
        """Load DecisionRecords from session for policy-layer consultation."""
        self._decision_records = []
        self._authorized_decisions = []
        if self._session_manager:
            session = self._session_manager.get_active_session(
                loop_state.current_mode, getattr(self, '_project_root', "")
            )
            if session:
                records = session.checkpoint.decisions.get("decision_records", [])
                if isinstance(records, list):
                    self._decision_records = [r for r in records if isinstance(r, str)]
                auth = session.checkpoint.decisions.get("authorized_decisions", [])
                if isinstance(auth, list):
                    self._authorized_decisions = [a for a in auth if isinstance(a, str)]

    def _classify_wave(self, loop_state: LoopState) -> None:
        """On first iteration, classify flow_type and assign/ mint wave.

        Single classification path for the engine (ADR 020).  The classifier
        model is resolved once in ``GraphBuilder.__init__`` and stored on
        ``self._classifier_model``; the same value binds the completion
        function and is passed to the call, so model and api_base can never
        diverge.  Wave lifetime comes from ``llm.wave``; the classification
        budget/temperature come from ``llm.classifier``.
        """
        if loop_state.iteration == 1 and self._project_root:
            try:
                from snodo.infrastructure.config import load_llm_config
                from snodo.infrastructure.wave_registry import WaveRegistry
                llm_cfg = load_llm_config()
                registry = WaveRegistry(
                    self._project_root,
                    config=llm_cfg.wave,
                    classifier=llm_cfg.classifier,
                )
                classifier_model = getattr(
                    self, "_classifier_model", None
                ) or self._default_model
                result = registry.classify_task(
                    loop_state.task.spec,
                    loop_state.task.id,
                    getattr(self, '_classifier_completion_fn', self._completion_fn),
                    classifier_model,
                )
                loop_state.task.flow_type = result.get("flow_type") or "feature"
                loop_state.task.wave_id = result.get("wave_id") or ""
                if result.get("task_summary"):
                    loop_state.metadata["task_summary"] = result["task_summary"]
                self._auto_write_classification(loop_state)
                # The classification must reach the audit trail: cloud_sync ships
                # audit events only, so a wave_id that lives solely in the session
                # checkpoint / job state.json never leaves the machine. An unwaved
                # task is legitimate (WaveRegistry returns None) and is emitted as
                # an empty wave_id — distinguishable from a failed classification,
                # which raises before this event is appended (Fixes #154).
                self._audit("task_classified", {
                    "op": "task_classified",
                    "task_ref": loop_state.task.id,
                    "flow_type": loop_state.task.flow_type,
                    "wave_id": loop_state.task.wave_id,
                    "task_summary": result.get("task_summary"),
                })
            except Exception as exc:
                import sys as _sys
                print(
                    f"[WAVE] classification failed for {loop_state.task.id}: {exc}",
                    file=_sys.stderr,
                )

    def _governance_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Stage 1: Check constraints and resolve pending disagreements."""
        loop_state = self._dict_to_state(state)
        loop_state.stage = LoopStage.GOVERNANCE
        loop_state.iteration += 1

        # Safety net: prevent infinite loops (max 50 iterations)
        if loop_state.iteration > 50:
            loop_state.is_blocked = True
            loop_state.halt_type = "max_iterations"
            loop_state.constraint_violations.append(
                "Max iterations (50) exceeded — possible infinite loop"
            )
            return self._state_to_dict(loop_state)

        # Spec-authoring follow-through: a warn-only pre-execute escalation routed
        # back here. Author an improved spec from the intent + critique, then let
        # it re-validate fresh. Lives in this (live) _governance_node because it
        # shadows the mixin version that holds the same logic.
        if getattr(loop_state, "needs_spec_authoring", False):
            loop_state = self._spec_authoring_reentry(loop_state)
            loop_state.needs_spec_authoring = False
            return self._state_to_dict(loop_state)

        # Load DecisionRecords from session for policy-layer consultation.
        # DecisionRecords are consulted AFTER the blocker HALT in the policy
        # evaluator, so they can NEVER override a genuine blocker (INV3).
        self._decision_records: List[str] = []
        self._authorized_decisions: List[str] = []
        if self._session_manager:
            session = self._session_manager.get_active_session(
                loop_state.current_mode, getattr(self, '_project_root', "")
            )
            if session:
                records = session.checkpoint.decisions.get("decision_records", [])
                if isinstance(records, list):
                    self._decision_records = [r for r in records if isinstance(r, str)]
                auth = session.checkpoint.decisions.get("authorized_decisions", [])
                if isinstance(auth, list):
                    self._authorized_decisions = [a for a in auth if isinstance(a, str)]

        # Respawn coder if a verified set_model(scope=coder) override exists
        self._maybe_respawn_coder()

        # Summarize messages if they've grown too large
        loop_state = self._maybe_summarize(loop_state)

        # On first iteration, classify flow_type and assign/ mint wave
        self._classify_wave(loop_state)

        # On first iteration, run environment preparation
        if loop_state.iteration == 1:
            try:
                from pathlib import Path
                from snodo.infrastructure.environment import prepare_environment, EnvironmentPrepError
                target_path = getattr(self, '_worktree_path', '') or getattr(self, '_project_root', '') or str(Path.cwd())
                prep_res = prepare_environment(
                    target_dir=target_path,
                    protocol=self.protocol,
                    shell_mcp=self.shell_mcp,
                )
                if prep_res.status == "executed":
                    self._progress(f"  Prepared environment: {prep_res.command}")
            except Exception as exc:
                if exc.__class__.__name__ == "EnvironmentPrepError" or isinstance(exc, EnvironmentPrepError):
                    loop_state.is_blocked = True
                    loop_state.halt_type = "validator_error"
                    loop_state.constraint_violations.append(str(exc))
                    cmd_val = getattr(exc, "command", "")
                    code_val = getattr(exc, "exit_code", 1)
                    out_val = getattr(exc, "output", str(exc))
                    self._audit("environment_prep_failed", {
                        "op": "environment_prep_failed",
                        "task_ref": loop_state.task.id,
                        "command": cmd_val,
                        "exit_code": code_val,
                        "output": out_val,
                    })
                    return self._state_to_dict(loop_state)
                raise

        loop_state = self.governance_fn(loop_state, self.protocol)

        self._audit("governance_check", {
            "op": "governance_check",
            "task_ref": loop_state.task.id,
            "mode": loop_state.current_mode,
            "constraints_checked": loop_state.constraints_passed,
        })

        # Track task in messages for agent memory (only on first iteration)
        if loop_state.iteration == 1:
            loop_state.messages.append({
                "role": "user",
                "content": f"Task: {loop_state.task.spec}"
            })

        return self._state_to_dict(loop_state)
