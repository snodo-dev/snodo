"""Canary tests for post-execute recovery blockers and required phase parameter in policy evaluation.

FILE: tests/engine/test_post_execute_recovery_blocker_gate.py
"""

import pytest
from snodo.compiler.models import DisagreementPolicy
from snodo.core.interfaces import ValidatorResult
from snodo.engine.policy import PolicyAction, PolicyEvaluator


def test_post_execute_recovery_blocker_halts_run():
    """A recovery-depth task (_fix_ task_ref, depth > 0) with a post-execute quality blocker must HALT."""
    evaluator = PolicyEvaluator()
    results = [
        ValidatorResult(
            validator_id="quality",
            severity="blocker",
            justification="make check failed with exit code 2",
        )
    ]

    # Post-execute evaluation on a recovery task
    decision = evaluator.evaluate(
        results,
        DisagreementPolicy.UNANIMOUS,
        phase="post_execute",
        task_ref="task1_fix_1",
        is_recovery=True,
    )

    assert decision.action == PolicyAction.HALT
    assert decision.blocker_count == 1
    assert "Pre-execute recovery finding" not in decision.justification


def test_evaluate_requires_phase_parameter():
    """PolicyEvaluator.evaluate requires phase as a non-default parameter."""
    evaluator = PolicyEvaluator()
    results = [
        ValidatorResult(validator_id="quality", severity="pass", justification="ok")
    ]

    with pytest.raises(TypeError):
        # Calling evaluate without phase parameter must raise TypeError
        evaluator.evaluate(results, DisagreementPolicy.UNANIMOUS)  # type: ignore[call-arg]
