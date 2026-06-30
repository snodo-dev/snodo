"""Governance node mixin.

FILE: snodo/engine/nodes/governance.py
"""

from typing import Any, Dict

from snodo.engine.state import LoopStage, LoopState


class GovernanceNodeMixin:
    """Mixin providing governance node capabilities to GraphBuilder."""

    def _spec_authoring_reentry(self, loop_state: LoopState) -> LoopState:
        """Author an improved spec from the INTENT + validator critique.

        Called when pre-execute validation escalated on warn-only spec
        validators.  One LLM call translates the raw intent into a proper
        spec (restated outcome, acceptance criteria, scope, intent +
        constraints).  Bounded to 2 attempts.
        """
        loop_state.spec_authoring_attempts += 1
        critique = loop_state.metadata.get("spec_critique", [])

        # Build spec-authoring prompt
        intent = loop_state.task.spec
        critique_text = "\n".join(
            f"- [{c.get('validator_id', '?')}] {c.get('justification', '')}"
            for c in critique
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

        self._audit("spec_authored", {
            "op": "spec_authored",
            "task_ref": loop_state.task.id,
            "attempt": loop_state.spec_authoring_attempts,
            "intent_preview": before[:400],
            "authored_spec_preview": authored_spec[:400],
            "critique": critique,
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
        """On first iteration, classify flow_type and assign/ mint wave."""
        if loop_state.iteration == 1 and self._project_root:
            try:
                from snodo.infrastructure.config import load_llm_config
                from snodo.infrastructure.wave_registry import WaveRegistry
                llm_cfg = load_llm_config()
                registry = WaveRegistry(self._project_root, config=llm_cfg.wave)
                classifier_model = (
                    llm_cfg.classifier.model
                    if llm_cfg.classifier and llm_cfg.classifier.model
                    else self._default_model
                )
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

        # Load DecisionRecords from session for policy-layer consultation.
        self._load_decision_records(loop_state)

        # Respawn coder if a verified set_model(scope=coder) override exists
        self._maybe_respawn_coder()

        # Summarize messages if they've grown too large
        loop_state = self._maybe_summarize(loop_state)

        # On first iteration, classify flow_type and assign/ mint wave
        self._classify_wave(loop_state)

        # Spec authoring re-entry: translate intent into a proper spec
        # when pre-execute validation escalated on warn-only validators.
        if loop_state.needs_spec_authoring:
            if loop_state.spec_authoring_attempts >= 2:
                # Cap reached — escalate to halt
                loop_state.is_blocked = True
                loop_state.halt_type = "escalated"
                loop_state.needs_spec_authoring = False
                loop_state.constraint_violations.append(
                    "Spec authoring exhausted after 2 attempts"
                )
            else:
                loop_state = self._spec_authoring_reentry(loop_state)

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
