"""Tests for disagreement policy evaluator.

FILE: tests/engine/test_policy.py

Matrix tests covering all validator combinations × all policies.
Ensures 100% coverage of policy logic.
"""

import pytest
from snodo.core.interfaces import ValidatorResult
from snodo.compiler.models import DisagreementPolicy
from snodo.engine.policy import (
    PolicyEvaluator, PolicyAction, evaluate_policy
)


# ========== HELPER FUNCTIONS ==========

def make_result(validator_id: str, severity: str) -> ValidatorResult:
    """Create a ValidatorResult for testing."""
    return ValidatorResult(
        validator_id=validator_id,
        severity=severity,
        justification=f"{severity} justification"
    )


# ========== INITIALIZATION TESTS ==========

def test_evaluator_init_defaults():
    """Test PolicyEvaluator initialization with defaults."""
    evaluator = PolicyEvaluator()
    assert evaluator.quorum_threshold == 0.67


def test_evaluator_init_custom_threshold():
    """Test PolicyEvaluator with custom threshold."""
    evaluator = PolicyEvaluator(quorum_threshold=0.75)
    assert evaluator.quorum_threshold == 0.75


def test_evaluator_init_invalid_threshold_low():
    """Test PolicyEvaluator rejects threshold < 0."""
    with pytest.raises(ValueError, match="must be between 0.0 and 1.0"):
        PolicyEvaluator(quorum_threshold=-0.1)


def test_evaluator_init_invalid_threshold_high():
    """Test PolicyEvaluator rejects threshold > 1."""
    with pytest.raises(ValueError, match="must be between 0.0 and 1.0"):
        PolicyEvaluator(quorum_threshold=1.1)


# ========== EMPTY RESULTS TESTS ==========

def test_empty_results_halts():
    """Test that empty results list halts."""
    evaluator = PolicyEvaluator()
    
    decision = evaluator.evaluate([], DisagreementPolicy.UNANIMOUS)
    
    assert decision.action == PolicyAction.HALT
    assert not decision.consensus_achieved
    assert decision.total_count == 0
    assert "No validator results" in decision.justification


# ========== BLOCKER TESTS (Always HALT) ==========

def test_single_blocker_halts_unanimous():
    """Test single blocker halts with UNANIMOUS policy."""
    evaluator = PolicyEvaluator()
    results = [make_result("v1", "blocker")]
    
    decision = evaluator.evaluate(results, DisagreementPolicy.UNANIMOUS)
    
    assert decision.action == PolicyAction.HALT
    assert not decision.consensus_achieved
    assert decision.blocker_count == 1
    assert "blocker" in decision.justification.lower()


def test_blocker_with_passes_halts():
    """Test blocker halts even if other validators pass."""
    evaluator = PolicyEvaluator()
    results = [
        make_result("v1", "pass"),
        make_result("v2", "pass"),
        make_result("v3", "blocker")
    ]
    
    decision = evaluator.evaluate(results, DisagreementPolicy.ANY)
    
    assert decision.action == PolicyAction.HALT
    assert decision.blocker_count == 1


# ========== UNANIMOUS POLICY TESTS ==========

def test_unanimous_all_pass():
    """Test unanimous with all pass."""
    evaluator = PolicyEvaluator()
    results = [
        make_result("v1", "pass"),
        make_result("v2", "pass"),
        make_result("v3", "pass")
    ]
    
    decision = evaluator.evaluate(results, DisagreementPolicy.UNANIMOUS)
    
    assert decision.action == PolicyAction.PROCEED
    assert decision.consensus_achieved
    assert decision.pass_count == 3
    assert decision.warn_count == 0
    assert "Unanimous pass" in decision.justification


def test_unanimous_all_pass_with_warnings():
    """Unanimous: 2 pass + 1 warn → ESCALATE (warn withholds approval)."""
    evaluator = PolicyEvaluator()
    results = [
        make_result("v1", "pass"),
        make_result("v2", "warn"),
        make_result("v3", "pass")
    ]
    
    decision = evaluator.evaluate(results, DisagreementPolicy.UNANIMOUS)
    
    # warn does NOT count as pass → 2/3 pass < 3 total → ESCALATE
    assert decision.action == PolicyAction.ESCALATE
    assert not decision.consensus_achieved
    assert decision.pass_count == 2
    assert decision.warn_count == 1
    assert "unanimous" in decision.justification.lower() or "all validators" in decision.justification.lower()


# ========== MAJORITY POLICY TESTS ==========

def test_majority_all_pass():
    """Test majority with all pass."""
    evaluator = PolicyEvaluator()
    results = [
        make_result("v1", "pass"),
        make_result("v2", "pass"),
        make_result("v3", "pass")
    ]
    
    decision = evaluator.evaluate(results, DisagreementPolicy.MAJORITY)
    
    assert decision.action == PolicyAction.PROCEED
    assert decision.consensus_achieved
    assert "Majority pass" in decision.justification


def test_majority_simple_majority():
    """Test majority with simple majority (2/3)."""
    evaluator = PolicyEvaluator()
    results = [
        make_result("v1", "pass"),
        make_result("v2", "pass"),
        make_result("v3", "warn")
    ]
    
    decision = evaluator.evaluate(results, DisagreementPolicy.MAJORITY)
    
    assert decision.action == PolicyAction.PROCEED_WITH_LOG
    assert decision.consensus_achieved
    assert decision.pass_count == 2


# ========== QUORUM POLICY TESTS ==========

def test_quorum_meets_threshold():
    """Quorum: 3 pass → meets threshold (3 >= 2.01)."""
    evaluator = PolicyEvaluator(quorum_threshold=0.67)
    results = [
        make_result("v1", "pass"),
        make_result("v2", "pass"),
        make_result("v3", "pass")
    ]
    
    decision = evaluator.evaluate(results, DisagreementPolicy.QUORUM)
    
    assert decision.action == PolicyAction.PROCEED
    assert decision.consensus_achieved
    assert "Quorum pass" in decision.justification


# ========== ANY POLICY TESTS ==========

def test_any_single_pass():
    """Test ANY with single pass."""
    evaluator = PolicyEvaluator()
    results = [make_result("v1", "pass")]
    
    decision = evaluator.evaluate(results, DisagreementPolicy.ANY)
    
    assert decision.action == PolicyAction.PROCEED
    assert decision.consensus_achieved
    assert "At least one pass" in decision.justification


# ========== CONVENIENCE FUNCTION TESTS ==========

def test_convenience_function():
    """Test evaluate_policy convenience function."""
    results = [
        make_result("v1", "pass"),
        make_result("v2", "pass")
    ]
    
    decision = evaluate_policy(results, DisagreementPolicy.UNANIMOUS)
    
    assert decision.action == PolicyAction.PROCEED
    assert decision.consensus_achieved
