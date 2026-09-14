"""Tests for disagreement policy evaluator.

FILE: tests/engine/test_policy.py

Matrix tests covering all validator combinations × all policies.
Ensures 100% coverage of policy logic.
"""

import pytest
from snodo.compiler.models import DisagreementPolicy
from snodo.core.interfaces import ValidatorResult
from snodo.engine.policy import PolicyAction, PolicyEvaluator, evaluate_policy

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


def test_policy_action_msgpack_registered(recwarn):
    """PolicyAction deserializes from langgraph JsonPlusSerializer without warnings (Fixes #43)."""
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    serde = JsonPlusSerializer()
    _, data = serde.dumps_typed(PolicyAction.PROCEED)
    loaded = serde.loads_typed(("msgpack", data))
    assert loaded == PolicyAction.PROCEED

    for w in recwarn.list:
        assert "PolicyAction" not in str(w.message)


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

    decision = evaluator.evaluate([], DisagreementPolicy.UNANIMOUS, phase="post_execute")

    assert decision.action == PolicyAction.HALT
    assert not decision.consensus_achieved
    assert decision.total_count == 0
    assert "No validator results" in decision.justification


# ========== BLOCKER TESTS (Always HALT) ==========

def test_single_blocker_halts_unanimous():
    """Test single blocker halts with UNANIMOUS policy."""
    evaluator = PolicyEvaluator()
    results = [make_result("v1", "blocker")]

    decision = evaluator.evaluate(results, DisagreementPolicy.UNANIMOUS, phase="post_execute")

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

    decision = evaluator.evaluate(results, DisagreementPolicy.ANY, phase="post_execute")

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

    decision = evaluator.evaluate(results, DisagreementPolicy.UNANIMOUS, phase="post_execute")

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

    decision = evaluator.evaluate(results, DisagreementPolicy.UNANIMOUS, phase="post_execute")

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

    decision = evaluator.evaluate(results, DisagreementPolicy.MAJORITY, phase="post_execute")

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

    decision = evaluator.evaluate(results, DisagreementPolicy.MAJORITY, phase="post_execute")

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

    decision = evaluator.evaluate(results, DisagreementPolicy.QUORUM, phase="post_execute")

    assert decision.action == PolicyAction.PROCEED
    assert decision.consensus_achieved
    assert "Quorum pass" in decision.justification


# ========== ANY POLICY TESTS ==========

def test_any_single_pass():
    """Test ANY with single pass."""
    evaluator = PolicyEvaluator()
    results = [make_result("v1", "pass")]

    decision = evaluator.evaluate(results, DisagreementPolicy.ANY, phase="post_execute")

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


# ========== RECOVERY PRE-EXECUTE TESTS ==========

def test_pre_execute_recovery_tree_state_finding_does_not_block():
    """In recovery (is_recovery=True), pre-execute warnings/blockers pass forward as evidence without blocking."""
    evaluator = PolicyEvaluator()
    results = [
        make_result("v1", "warn"),
        make_result("v2", "blocker"),
    ]

    decision = evaluator.evaluate(
        results, DisagreementPolicy.UNANIMOUS, is_recovery=True, phase="pre_execute"
    )

    assert decision.action == PolicyAction.PROCEED_WITH_LOG
    assert decision.consensus_achieved
    assert "Pre-execute recovery finding(s)" in decision.justification


def test_pre_execute_recovery_operational_error_still_halts():
    """Operational errors (error=True) during pre-execute recovery still halt fail-closed."""
    evaluator = PolicyEvaluator()
    err_result = ValidatorResult(
        validator_id="v1",
        severity="blocker",
        justification="Internal error",
        error=True,
    )

    decision = evaluator.evaluate(
        [err_result], DisagreementPolicy.UNANIMOUS, is_recovery=True, phase="pre_execute"
    )

    assert decision.action == PolicyAction.HALT
    assert not decision.consensus_achieved
    assert "fail-closed" in decision.justification


def test_post_execute_recovery_finding_blocks_normally():
    """Post-execute findings in recovery evaluate normally and block under UNANIMOUS policy."""
    evaluator = PolicyEvaluator()
    results = [make_result("v1", "blocker")]

    decision = evaluator.evaluate(
        results, DisagreementPolicy.UNANIMOUS, is_recovery=True, phase="post_execute"
    )

    assert decision.action == PolicyAction.HALT
    assert not decision.consensus_achieved
    assert "1 blocker(s) present" in decision.justification


def test_run_validators_pre_execute_recovery_caps_severity():
    """run_validators caps pre-execute recovery findings to pass while preserving original severity."""
    from snodo.compiler.models import Mode, Protocol, Validator
    from snodo.core.interfaces import Task
    from snodo.validators.runner import run_validators

    v = Validator(validator_id="arch", validator_type="quality", evaluation_phase="pre_execute")
    mode = Mode(mode_id="test", name="test", tools=[], validators=["arch"])
    protocol = Protocol(protocol_id="test", name="test", initial_mode="test", modes=[mode], validators=[v])
    task = Task(id="t1", spec="test task", depth=1)

    def mock_dispatch(val, ctx, reg):
        return ValidatorResult(validator_id="arch", severity="warn", justification="stale CSS rule")

    results, cap_originals = run_validators(
        protocol=protocol,
        validators=[v],
        task=task,
        phase="pre_execute",
        dispatch_fn=mock_dispatch,
    )

    assert len(results) == 1
    assert results[0].severity == "pass"
    assert "Pre-execute recovery finding (warn)" in results[0].justification
    assert cap_originals["arch"] == "warn"


def make_abstention(validator_id: str) -> ValidatorResult:
    """Create an abstention: a judge that reached no verdict (severity None)."""
    return ValidatorResult(
        validator_id=validator_id,
        severity=None,
        justification="Validator could not reach a verdict within its turn budget.",
        abstention_reason="exhausted budget",
    )


# ========== ABSTENTION POLICY TESTS (Fixes #277) ==========
#
# The documented contract: `non_blocking` excludes abstentions from the policy
# counts, so the threshold applies to the judges that decided. The denominator
# shrinks; abstainers are reported in abstain_count, never converted to a pass.

# One abstainer, the rest passing. Under non_blocking every policy must proceed
# on the judges that decided; under blocking every policy must halt.

_POLICIES = [
    DisagreementPolicy.UNANIMOUS,
    DisagreementPolicy.MAJORITY,
    DisagreementPolicy.QUORUM,
    DisagreementPolicy.ANY,
]


@pytest.mark.parametrize("policy", _POLICIES)
def test_non_blocking_abstention_excluded_from_denominator(policy):
    """One abstainer, two passing: non_blocking proceeds under every policy, and
    the counts honestly report only the two judges that voted."""
    evaluator = PolicyEvaluator(abstention_policy="non_blocking")
    results = [
        make_result("v1", "pass"),
        make_result("v2", "pass"),
        make_abstention("v3"),
    ]

    decision = evaluator.evaluate(results, policy, phase="post_execute")

    assert decision.action in (PolicyAction.PROCEED, PolicyAction.PROCEED_WITH_LOG)
    assert decision.consensus_achieved
    assert decision.pass_count == 2
    assert decision.total_count == 2
    assert decision.abstain_count == 1


@pytest.mark.parametrize("policy", _POLICIES)
def test_blocking_abstention_halts_every_policy(policy):
    """The same quorum under the default blocking policy halts: the abstention
    is not escapable by the disagreement policy."""
    evaluator = PolicyEvaluator(abstention_policy="blocking")
    results = [
        make_result("v1", "pass"),
        make_result("v2", "pass"),
        make_abstention("v3"),
    ]

    decision = evaluator.evaluate(results, policy, phase="post_execute")

    assert decision.action == PolicyAction.HALT
    assert not decision.consensus_achieved
    assert decision.abstain_count == 1
    # Blocking reports the full quorum it halted: the abstainer is in the
    # denominator because it is precisely what stopped the run.
    assert decision.total_count == 3


def test_unanimous_non_blocking_single_abstainer_alone_proceeds():
    """The exact observed defect: a unanimous protocol whose only non-pass
    result is an abstention proceeds under non_blocking."""
    evaluator = PolicyEvaluator(abstention_policy="non_blocking")
    results = [make_result("meta-spec", "pass"),
               make_result("security", "pass"),
               make_abstention("arch")]

    decision = evaluator.evaluate(results, DisagreementPolicy.UNANIMOUS,
                                  phase="pre_execute")

    assert decision.action == PolicyAction.PROCEED
    assert decision.total_count == 2
    assert decision.pass_count == 2


def test_abstention_is_never_counted_as_a_pass():
    """An abstention moves no count except abstain_count — in particular it is
    never a pass, under either abstention policy."""
    results = [make_result("v1", "pass"), make_abstention("v2")]

    for policy_name in ("blocking", "non_blocking"):
        evaluator = PolicyEvaluator(abstention_policy=policy_name)
        decision = evaluator.evaluate(results, DisagreementPolicy.UNANIMOUS,
                                      phase="post_execute")
        assert decision.pass_count == 1
        assert decision.abstain_count == 1
        assert decision.abstain_count + decision.pass_count + decision.warn_count \
            + decision.blocker_count == len(results)
        if policy_name == "non_blocking":
            assert decision.total_count == 1
        else:
            assert decision.total_count == 2


def test_non_blocking_all_abstain_halts_not_unanimous():
    """An empty denominator must not read as unanimity: with no judge deciding
    there is no verdict to found a decision on, so the run halts."""
    evaluator = PolicyEvaluator(abstention_policy="non_blocking")
    results = [make_abstention("v1"), make_abstention("v2")]

    decision = evaluator.evaluate(results, DisagreementPolicy.UNANIMOUS,
                                  phase="post_execute")

    assert decision.action == PolicyAction.HALT
    assert not decision.consensus_achieved
    assert decision.pass_count == 0
    assert decision.total_count == 0
    assert decision.abstain_count == 2


def test_non_blocking_blocker_still_halts():
    """A genuine blocker is not made escapable by non_blocking."""
    evaluator = PolicyEvaluator(abstention_policy="non_blocking")
    results = [make_result("v1", "pass"), make_result("v2", "blocker"),
               make_abstention("v3")]

    decision = evaluator.evaluate(results, DisagreementPolicy.ANY,
                                  phase="post_execute")

    assert decision.action == PolicyAction.HALT
    assert decision.blocker_count == 1


def test_non_blocking_validator_error_still_halts():
    """A validator error remains fail-closed under non_blocking."""
    evaluator = PolicyEvaluator(abstention_policy="non_blocking")
    err = ValidatorResult(validator_id="v1", severity=None,
                          justification="internal error", error=True)
    results = [make_result("v2", "pass"), err, make_abstention("v3")]

    decision = evaluator.evaluate(results, DisagreementPolicy.UNANIMOUS,
                                  phase="post_execute")

    assert decision.action == PolicyAction.HALT
    assert "fail-closed" in decision.justification

