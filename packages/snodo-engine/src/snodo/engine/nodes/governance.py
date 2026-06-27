"""Governance node mixin.

FILE: snodo/engine/nodes/governance.py
"""

from typing import Dict, Any
from snodo.engine.state import LoopStage, LoopState


class GovernanceNodeMixin:
    """Mixin providing governance node capabilities to GraphBuilder."""

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
                from snodo.infrastructure.wave_registry import WaveRegistry
                from snodo.infrastructure.config import load_llm_config
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
                    self._completion_fn,
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
