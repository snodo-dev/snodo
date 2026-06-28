"""Serde node mixin.

FILE: snodo/engine/nodes/state.py
"""

from typing import Dict, Any
from snodo.core.interfaces import Task, ValidatorResult
from snodo.infrastructure.tokens import ValidationToken
from snodo.engine.state import LoopState, LoopStage
from snodo.engine.policy import policy_decision_to_dict


class SerdeMixin:
    """Mixin providing loop state serialization and deserialization."""

    def _dict_to_state(self, d: Dict[str, Any]) -> LoopState:
        """Convert dict to LoopState."""
        task_dict = d.get("task", {})
        task = Task(
            id=task_dict.get("id", ""),
            spec=task_dict.get("spec", ""),
            parent_task_ref=task_dict.get("parent_task_ref"),
            depth=task_dict.get("depth", 0),
            flow_type=task_dict.get("flow_type"),
            wave_id=task_dict.get("wave_id"),
        )
        
        results = []
        for r in d.get("validation_results", []):
            results.append(ValidatorResult(
                validator_id=r.get("validator_id", ""),
                severity=r.get("severity", "pass"),
                justification=r.get("justification", "")
            ))
        
        token = None
        if d.get("validation_token"):
            token_dict = d["validation_token"]
            jwt_str = token_dict.get("jwt", "")
            if jwt_str:
                # Reconstruct token from JWT string via the issuer
                token = ValidationToken(jwt=jwt_str)
                payload = self._token_issuer.decode_token(token)
                if payload:
                    token = ValidationToken(
                        jwt=jwt_str,
                        task_id=payload.get("task_id", ""),
                        validator_signatures=payload.get("validator_signatures", []),
                        consensus=payload.get("consensus", ""),
                        issued_at=payload.get("iat", ""),
                        expires_at=payload.get("exp", ""),
                    )
        
        return LoopState(
            task=task,
            current_mode=d.get("current_mode", ""),
            validation_results=results,
            validation_token=token,
            artifacts=d.get("artifacts", []),
            stage=LoopStage(d.get("stage", "governance")),
            iteration=d.get("iteration", 0),
            constraints_passed=d.get("constraints_passed", True),
            constraint_violations=d.get("constraint_violations", []),
            policy_decision=d.get("policy_decision"),
            is_complete=d.get("is_complete", False),
            is_blocked=d.get("is_blocked", False),
            halt_type=d.get("halt_type"),
            pending_disagreement=d.get("pending_disagreement"),
            spawned_subtasks=[
                Task(
                    id=s.get("id", ""),
                    spec=s.get("spec", ""),
                    parent_task_ref=s.get("parent_task_ref"),
                    depth=s.get("depth", 0),
                    flow_type=s.get("flow_type"),
                    wave_id=s.get("wave_id"),
                )
                for s in d.get("spawned_subtasks", [])
            ],
            metadata=d.get("metadata", {}),
            messages=d.get("messages", []),
            summary=d.get("summary", ""),
            needs_recovery=d.get("needs_recovery", False),
        )

    def _state_to_dict(self, state: LoopState) -> Dict[str, Any]:
        """Convert LoopState to dict."""
        return {
            "task": {
                "id": state.task.id,
                "spec": state.task.spec,
                "parent_task_ref": state.task.parent_task_ref,
                "depth": state.task.depth,
                "flow_type": state.task.flow_type,
                "wave_id": state.task.wave_id,
            },
            "current_mode": state.current_mode,
            "validation_results": [
                {
                    "validator_id": r.validator_id,
                    "severity": r.severity,
                    "justification": r.justification
                }
                for r in state.validation_results
            ],
            "validation_token": {
                "jwt": state.validation_token.jwt,
            } if state.validation_token else None,
            "artifacts": state.artifacts,
            "stage": state.stage.value,
            "iteration": state.iteration,
            "constraints_passed": state.constraints_passed,
            "constraint_violations": state.constraint_violations,
            "policy_decision": policy_decision_to_dict(state.policy_decision),
            "is_complete": state.is_complete,
            "is_blocked": state.is_blocked,
            "halt_type": state.halt_type,
            "pending_disagreement": state.pending_disagreement,
            "metadata": state.metadata,
            "messages": state.messages,
            "summary": state.summary,
            "spawned_subtasks": [
                {
                    "id": s.id,
                    "spec": s.spec,
                    "parent_task_ref": s.parent_task_ref,
                    "depth": s.depth,
                    "flow_type": s.flow_type,
                    "wave_id": s.wave_id,
                }
                for s in state.spawned_subtasks
            ],
            "needs_recovery": state.needs_recovery,
        }
