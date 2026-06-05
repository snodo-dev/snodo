"""Constraint evaluation engine for protocol governance.

Extracted from engine/loop.py to isolate constraint logic.
"""

from typing import TYPE_CHECKING, Any, List

from snodo.compiler.models import Protocol

if TYPE_CHECKING:
    from snodo.engine.loop import LoopState


class ConstraintEngine:
    """Evaluates protocol and mode constraints against execution context."""

    def __init__(
        self,
        protocol: Protocol,
        predicate_registry: Any,
        workspace_mcp: Any,
        git_mcp: Any,
    ):
        self.protocol = protocol
        self._predicate_registry = predicate_registry
        self.workspace_mcp = workspace_mcp
        self.git_mcp = git_mcp

    def evaluate(self, state: "LoopState", phase: str, audit_fn) -> "LoopState":
        """Evaluate applicable constraints and update state.

        Args:
            state: Current loop state
            phase: Constraint phase ("governance", "post_validate", etc.)
            audit_fn: Callable(event_type, data) for audit logging
        """
        state.constraints_passed = True
        state.constraint_violations = []

        # Collect applicable constraints
        constraints: List[Any] = list(self.protocol.global_constraints)
        mode = self.protocol.get_mode(state.current_mode)
        if mode:
            constraints.extend(mode.constraints)

        if not constraints:
            return state

        from snodo.predicates.base import PredicateContext

        ctx = PredicateContext(
            task=state.task,
            mode=state.current_mode,
            artifacts=state.artifacts,
            workspace_mcp=self.workspace_mcp,
            git_mcp=self.git_mcp,
            protocol=self.protocol,
            phase=phase,
        )

        for constraint in constraints:
            if not constraint.predicate:
                continue

            try:
                pred = self._predicate_registry.lookup(constraint.predicate)
            except KeyError:
                audit_fn("constraint_predicate_unknown", {
                    "op": "constraint_predicate_unknown",
                    "constraint_id": constraint.constraint_id,
                    "predicate_name": constraint.predicate,
                    "phase": phase,
                })
                self._apply_failure(
                    state, constraint, "blocker",
                    f"Unknown predicate: {constraint.predicate}",
                )
                continue

            try:
                result = pred.evaluate(ctx, **constraint.params)
            except Exception as e:
                result = type("_R", (), {
                    "passed": False,
                    "justification": f"Predicate evaluation error: {e}",
                    "evidence": {},
                })

            if result.passed:
                continue

            self._apply_failure(
                state, constraint, constraint.severity.value, result.justification,
            )
            audit_fn("constraint_violation", {
                "op": "constraint_violation",
                "constraint_id": constraint.constraint_id,
                "predicate": constraint.predicate,
                "severity": constraint.severity.value,
                "justification": result.justification,
                "evidence": result.evidence,
                "phase": phase,
            })

        return state

    @staticmethod
    def _apply_failure(
        state: "LoopState",
        constraint: Any,
        severity: str,
        justification: str,
    ) -> None:
        """Apply a constraint failure to the loop state."""
        msg = f"{constraint.constraint_id}: {justification}"
        state.constraint_violations.append(msg)
        if severity == "blocker":
            state.is_blocked = True
            state.halt_type = "constraint"
            state.constraints_passed = False
