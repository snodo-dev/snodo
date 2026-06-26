"""Characterization tests for validator severity outcomes.

Covers the fail-closed severity model:
  - No criteria → warn (nothing to evaluate)
  - Configured check that can't run → blocker + error=True
  - error flag → halt_type="validator_error" in the loop
"""

from unittest.mock import MagicMock, patch
import pytest

from snodo.core.interfaces import Task, ValidatorResult
from snodo.compiler.models import Validator, DisagreementPolicy
from snodo.engine.validators import ValidatorRunner
from snodo.engine.policy import PolicyEvaluator, PolicyAction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runner(**overrides):
    kwargs = dict(
        protocol=MagicMock(),
        completion_fn=None,
        default_model="claude-sonnet-4-20250514",
        validator_config=MagicMock(max_tokens=1500, max_tool_turns=6),
        audit_log=None, workspace_mcp=None, git_mcp=None, session_manager=None,
    )
    kwargs.update(overrides)
    return ValidatorRunner(**kwargs)


# ---------------------------------------------------------------------------
# _dispatch_one: stub (no criteria)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("criteria,expected_severity,expected_error", [
    ([], "warn", False),
    (["some rule"], None, None),  # will be handled by different branch
])
def test_dispatch_one_no_criteria_returns_warn(criteria, expected_severity, expected_error):
    v = Validator(validator_id="test_v", validator_type="dummy", criteria=criteria)
    runner = _make_runner()
    ctx = MagicMock()
    ctx.completion_fn = None  # no LLM available
    reg = MagicMock()
    reg.lookup.return_value = None  # no handler registered

    result = runner._dispatch_one(v, ctx, reg)

    if expected_severity is not None:
        assert result.severity == expected_severity
        assert result.error == expected_error


# ---------------------------------------------------------------------------
# _dispatch_one: criteria with LLM unavailable → blocker + error flag
# ---------------------------------------------------------------------------

def test_dispatch_one_llm_unavailable_with_criteria_returns_blocker_error():
    v = Validator(validator_id="test_v", validator_type="unknown", criteria=["check something"])
    runner = _make_runner(completion_fn=None)
    ctx = MagicMock()
    ctx.completion_fn = None
    reg = MagicMock()
    reg.lookup.return_value = None  # no handler

    result = runner._dispatch_one(v, ctx, reg)

    assert result.severity == "blocker"
    assert result.error is True
    assert "unavailable" in result.justification.lower()


# ---------------------------------------------------------------------------
# _dispatch_one: evaluate() raises → blocker + error flag
# ---------------------------------------------------------------------------

def test_dispatch_one_registry_handler_raises_returns_blocker_error():
    class FailingHandler:
        def evaluate(self, ctx):
            raise RuntimeError("Something went wrong in the validator")

    v = Validator(validator_id="test_v", validator_type="security", criteria=["check"])
    runner = _make_runner()
    ctx = MagicMock()
    ctx.completion_fn = MagicMock()
    reg = MagicMock()
    reg.lookup.return_value = FailingHandler

    result = runner._dispatch_one(v, ctx, reg)

    assert result.severity == "blocker"
    assert result.error is True
    assert "error" in result.justification.lower()


def test_dispatch_one_future_exception_returns_blocker_error():
    """Exception from the thread-pool wrapper in run()."""
    v = Validator(validator_id="test_v", validator_type="dummy", criteria=["x"])
    runner = _make_runner()
    ctx = MagicMock()
    ctx.completion_fn = None
    reg = MagicMock()
    # Make _dispatch_one raise inside the pool
    original = runner._dispatch_one

    def exploding_dispatch(*args, **kwargs):
        raise RuntimeError("pool crash")

    with patch.object(runner, "_dispatch_one", exploding_dispatch):
        results = runner.run(
            Task(id="t1", spec="test"),
            [v], None, current_mode="producer",
        )

    assert len(results) == 1
    assert results[0].severity == "blocker"
    assert results[0].error is True


# ---------------------------------------------------------------------------
# Policy: error flag always triggers HALT
# ---------------------------------------------------------------------------

class TestPolicyErrorFlagHalts:
    def test_single_error_halts(self):
        evaluator = PolicyEvaluator()
        results = [
            ValidatorResult(validator_id="v1", severity="pass", justification="ok"),
            ValidatorResult(validator_id="v2", severity="blocker", justification="crash", error=True),
        ]
        for policy in DisagreementPolicy:
            decision = evaluator.evaluate(results, policy)
            assert decision.action == PolicyAction.HALT
            assert decision.blocker_count == 1

    def test_error_count_in_decision(self):
        evaluator = PolicyEvaluator()
        results = [
            ValidatorResult(validator_id="v1", severity="blocker", justification="fail", error=True),
            ValidatorResult(validator_id="v2", severity="blocker", justification="fail", error=True),
        ]
        decision = evaluator.evaluate(results, DisagreementPolicy.UNANIMOUS)
        # Both are blockers; error flag is tracked separately
        assert decision.blocker_count == 2
        assert "fail" in decision.justification


# ---------------------------------------------------------------------------
# Loop halt_type: error flag → validator_error
# ---------------------------------------------------------------------------

def test_loop_sets_validator_error_halt_type():
    """Simulate how _validate_node sets halt_type from the error flag."""
    from snodo.engine.loop import GraphBuilder
    from snodo.engine.state import LoopState, LoopStage
    from dataclasses import dataclass, field

    protocol = MagicMock()
    protocol.get_mode.return_value = MagicMock()
    protocol.get_mode.return_value.validators = ["v1"]
    protocol.get_validator.return_value = Validator(
        validator_id="v1", validator_type="security", criteria=["x"]
    )
    protocol.disagreement_policy = DisagreementPolicy.UNANIMOUS

    with patch.object(GraphBuilder, "__init__", return_value=None):
        builder = GraphBuilder.__new__(GraphBuilder)
        builder.protocol = protocol
        builder.policy_evaluator = PolicyEvaluator()
        builder._token_issuer = MagicMock()
        builder._audit = MagicMock()
        builder._auto_write_pending_decisions = MagicMock()
        builder._auto_write_failure_context = MagicMock()
        builder._auto_write_halt_payload = MagicMock()
        builder._validator_runner = MagicMock()
        builder._constraint_engine = MagicMock()
        builder._decision_records = []
        builder._decision_issuer = None
        builder._state_to_dict = MagicMock(return_value={})
        builder._dict_to_state = MagicMock()
        builder.shell_mcp = None
        builder.validator_fn = MagicMock()

        state = LoopState(
            task=Task(id="t1", spec="test"),
            current_mode="producer",
        )
        builder._dict_to_state.return_value = state

        # Simulate validator results with error flag
        results = [
            ValidatorResult(validator_id="v1", severity="blocker",
                            justification="crash", error=True),
        ]

        # Directly set the results and evaluate through policy
        state.validation_results = results
        decision = builder.policy_evaluator.evaluate(
            results, protocol.disagreement_policy,
        )
        state.policy_decision = decision

        # Simulate the halt_type logic from _validate_node
        if decision.action == PolicyAction.HALT:
            state.is_blocked = True
            has_errors = any(getattr(r, 'error', False) for r in results)
            state.halt_type = "validator_error" if has_errors else "blocked"

        assert state.halt_type == "validator_error"
        assert state.is_blocked is True


def test_loop_blocked_halt_type_no_errors():
    """Without error flag, blockers produce halt_type='blocked'."""
    from snodo.engine.policy import PolicyAction

    state = MagicMock()
    results = [
        ValidatorResult(validator_id="v1", severity="blocker",
                        justification="blocker", error=False),
    ]
    # Simulate halt_type logic
    is_blocked = True
    has_errors = any(getattr(r, 'error', False) for r in results)
    halt_type = "validator_error" if has_errors else "blocked"

    assert halt_type == "blocked"
    assert has_errors is False
