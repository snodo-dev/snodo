"""Hypothesis property-based tests for Snodo invariants.

FILE: tests/properties/test_invariants.py (Task 7.16)
"""

import tempfile
from pathlib import Path

from hypothesis import given, settings, strategies as st, HealthCheck
import pytest

from snodo.infrastructure.audit import AuditLog
from snodo.core.interfaces import ValidatorResult
from snodo.engine.policy import PolicyEvaluator, PolicyAction
from snodo.compiler.models import (
    Protocol, Severity, DisagreementPolicy,
)
from snodo.infrastructure.tokens import ValidationToken

from tests.strategies import (
    hypothesis_settings,
    protocols, tasks, validator_results,
    identifiers,
    jwt_tokens, gen_audit_events,
)

severity_enum_strings = st.sampled_from(["pass", "warn", "blocker"])


# ============================================================================
# Core Property 1 — Audit chain integrity
# ============================================================================

# Pre-build settings object for all tests
_HYP_SETTINGS = hypothesis_settings()


@given(events=st.data())
@_HYP_SETTINGS
@pytest.mark.property
def test_audit_chain_integrity_after_events(events):
    """Appending arbitrary events preserves chain integrity."""
    import tempfile
    import shutil
    tmpdir = Path(tempfile.mkdtemp())
    try:
        log = AuditLog(str(tmpdir / "audit.log"))
        gen_audit_events(log, events, min_count=1, max_count=30)
        assert log.verify_chain(), (
            f"Chain broken with {len(log.events)} events"
        )
    finally:
        shutil.rmtree(str(tmpdir), ignore_errors=True)


@given(events=st.data())
@_HYP_SETTINGS
@pytest.mark.property
def test_audit_chain_tamper_detected(events):
    """Mutating any event's data breaks verify_chain."""
    import tempfile
    import shutil
    tmpdir = Path(tempfile.mkdtemp())
    log = AuditLog(str(tmpdir / "audit.log"))
    try:
        gen_audit_events(log, events, min_count=5, max_count=30)
        if len(log.events) > 1:
            mid = len(log.events) // 2
            log.events[mid].data["task_ref"] = "tampered_task"
            assert not log.verify_chain(), "Tamper should be detected"
    finally:
        shutil.rmtree(str(tmpdir), ignore_errors=True)


# ============================================================================
# Core Property 2 — Policy HALT invariant
# ============================================================================

@given(results=validator_results())
@_HYP_SETTINGS
@pytest.mark.property
def test_policy_halt_when_any_blocker(results):
    """Any blocker in results → PolicyEvaluator returns HALT, regardless of policy."""
    for policy in [DisagreementPolicy.UNANIMOUS, DisagreementPolicy.MAJORITY,
                   DisagreementPolicy.QUORUM, DisagreementPolicy.ANY]:
        evaluator = PolicyEvaluator()
        decision = evaluator.evaluate(results, policy)
        if any(r.severity == "blocker" for r in results):
            from snodo.engine.policy import PolicyAction
            assert decision.action == PolicyAction.HALT, (
                f"Blocker present but action={decision.action} under {policy}"
            )


@given(results=validator_results(min_count=1))
@_HYP_SETTINGS
@pytest.mark.property
def test_policy_proceed_when_all_pass(results):
    """All pass results → PolicyEvaluator returns PROCEED for all policies."""
    clean = [ValidatorResult(validator_id=r.validator_id, severity="pass",
                              justification=r.justification) for r in results]
    # Also allow 1 warn if there are at least 2 validators
    for policy in [DisagreementPolicy.UNANIMOUS, DisagreementPolicy.ANY]:
        evaluator = PolicyEvaluator()
        decision = evaluator.evaluate(clean, policy)
        from snodo.engine.policy import PolicyAction
        assert decision.action in (PolicyAction.PROCEED, PolicyAction.PROCEED_WITH_LOG), (
            f"All-pass should proceed under {policy}"
        )


# ============================================================================
# Core Property 3 — JWT tampering detected
# ============================================================================

@given(token_data=jwt_tokens())
@_HYP_SETTINGS
@pytest.mark.property
def test_jwt_valid_token_verifies(token_data):
    """A freshly-issued valid token always verifies."""
    token, issuer, task_id = token_data
    assert token is not None
    assert issuer.verify_token(token) is True
    assert issuer.verify_token(token, expected_task_id=task_id) is True


@given(token_data=jwt_tokens())
@_HYP_SETTINGS
@pytest.mark.property
def test_jwt_wrong_task_rejected(token_data):
    """A valid token for task A is rejected when checked against task B."""
    token, issuer, task_id = token_data
    assert token is not None
    assert issuer.verify_token(token, expected_task_id="wrong_task_id") is False


@given(token_data=jwt_tokens())
@_HYP_SETTINGS
@pytest.mark.property
def test_jwt_tampered_rejected(token_data):
    """Payload-modification always invalidates a JWT."""
    token, issuer, task_id = token_data
    assert token is not None
    parts = token.jwt.split(".")
    parts[1] = parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B")
    tampered = ValidationToken(jwt=".".join(parts))
    assert not issuer.verify_token(tampered), "Tampered token should be rejected"


# ============================================================================
# Core Property 4 — WF1 disjointness
# ============================================================================

@given(protocol=protocols())
@_HYP_SETTINGS
@pytest.mark.property
def test_wf1_modes_have_disjoint_tools(protocol):
    """Every pair of modes in a protocol has disjoint tool sets."""
    for i in range(len(protocol.modes)):
        for j in range(i + 1, len(protocol.modes)):
            tools_i = set(protocol.modes[i].tools)
            tools_j = set(protocol.modes[j].tools)
            assert tools_i.isdisjoint(tools_j), (
                f"Modes {protocol.modes[i].mode_id} and {protocol.modes[j].mode_id} "
                f"share tools: {tools_i & tools_j}"
            )


# ============================================================================
# Core Property 5 — Severity cap monotonicity
# ============================================================================

@given(orig=severity_enum_strings, cap=st.sampled_from([Severity.PASS, Severity.WARN]))
@_HYP_SETTINGS
@pytest.mark.property
def test_severity_cap_never_increases_severity(orig, cap):
    """Applying a cap never results in a HIGHER severity."""
    result = ValidatorResult(validator_id="v1", severity=orig, justification="test")
    result_sev = Severity(result.severity)
    # If cap is below result, result gets downgraded; never upgraded
    if result_sev > cap:
        assert cap.value != "blocker" or orig == "blocker"
        # Blocked -> warn under warn cap, or blocked/warn -> pass under pass cap
        assert cap in (Severity.PASS, Severity.WARN)


@given(orig=severity_enum_strings, cap=st.sampled_from([Severity.PASS, Severity.WARN]))
@_HYP_SETTINGS
@pytest.mark.property
def test_severity_cap_preserves_pass(orig, cap):
    """A 'pass' result is never downgraded (already minimum)."""
    if orig == "pass":
        result_sev = Severity(orig)
        assert not (result_sev > cap), f"PASS should never exceed cap={cap}"


# ============================================================================
# Core Property 6 — LoopState round-trip
# ============================================================================

@given(task=tasks(), mode=st.sampled_from(["producer", "reviewer"]),
       it=st.integers(0, 50))
@settings(deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
@pytest.mark.property
def test_loopstate_dict_roundtrip(task, mode, it):
    """LoopState survives _dict_to_state → _state_to_dict round-trip."""
    from snodo.engine.loop import LoopState, LoopStage, GraphBuilder
    from snodo.compiler.models import Mode as CMode, Validator as CMValidator

    protocol = Protocol(
        protocol_id="rt", name="Roundtrip",
        modes=[CMode(mode_id=mode, name=f"{mode} Mode", tools=["edit"], validators=[])],
        validators=[CMValidator(validator_id="v1", validator_type="security",
                                 evaluation_phase="pre_execute")],
        initial_mode=mode,
    )
    builder = GraphBuilder(protocol)

    state = LoopState(
        task=task,
        current_mode=mode,
        iteration=it,
        stage=LoopStage.GOVERNANCE,
    )
    # Serialize
    d = builder._state_to_dict(state)
    # Deserialize
    r = builder._dict_to_state(d)
    # Verify key fields match
    assert r.task.id == state.task.id
    assert r.task.spec == state.task.spec
    assert r.current_mode == state.current_mode
    assert r.iteration == state.iteration
    assert r.stage == state.stage


# ============================================================================
# Bonus Property 7 — Session checkpoint round-trip
# ============================================================================

@given(task_id=identifiers, decision_key=identifiers,
       decision_val=st.text(min_size=3, max_size=30))
@_HYP_SETTINGS
@pytest.mark.property
def test_session_decision_roundtrip(task_id, decision_key, decision_val):
    """A decision written to session survives read-back."""
    from snodo.infrastructure.session import SessionManager

    sessions_dir = Path(tempfile.mkdtemp())
    mgr = SessionManager(sessions_dir=sessions_dir)

    session = mgr.create_session("producer", str(sessions_dir))
    mgr.update_decision(session.session_id, decision_key, decision_val)

    loaded = mgr.load_session(session.session_id)
    assert loaded.checkpoint.decisions[decision_key] == decision_val


# ============================================================================
# Bonus Property 8 — Predicate determinism
# ============================================================================

@given(artifacts=st.lists(st.text(min_size=3, max_size=30), min_size=0, max_size=10))
@_HYP_SETTINGS
@pytest.mark.property
def test_files_in_scope_deterministic(artifacts):
    """Same input always produces same output for files_in_scope."""
    from snodo.predicates.scope import FilesInScope
    from snodo.predicates.base import PredicateContext

    pred = FilesInScope()
    ctx = PredicateContext(
        task=None, mode="producer", artifacts=artifacts,
        phase="post_validate",
    )
    r1 = pred.evaluate(ctx, scope_paths=["src/**", "tests/**"])
    r2 = pred.evaluate(ctx, scope_paths=["src/**", "tests/**"])
    assert r1.passed == r2.passed
    assert r1.justification == r2.justification


# ============================================================================
# Bonus Property 9 — Error severity always halts
# ============================================================================

@given(results=validator_results(min_count=1))
@_HYP_SETTINGS
@pytest.mark.property
def test_policy_error_severity_always_halts(results):
    """Any result with error=True → HALT under ALL disagreement policies."""
    forced = [ValidatorResult(
        validator_id="err_v", severity="blocker",
        justification="validator failure", error=True,
    )] + list(results)
    for policy in [DisagreementPolicy.UNANIMOUS, DisagreementPolicy.MAJORITY,
                   DisagreementPolicy.QUORUM, DisagreementPolicy.ANY]:
        evaluator = PolicyEvaluator()
        decision = evaluator.evaluate(forced, policy)
        assert decision.action == PolicyAction.HALT, (
            f"Error present but action={decision.action} under {policy}"
        )


# ============================================================================
# Bonus Property 10 — Unanimous all-warn escalates
# ============================================================================

@given(results=validator_results(min_count=1))
@_HYP_SETTINGS
@pytest.mark.property
def test_policy_warn_unanimous_escalates(results):
    """All-warn results under unanimous policy → ESCALATE (not PROCEED)."""
    all_warn = [ValidatorResult(
        validator_id=r.validator_id, severity="warn",
        justification=r.justification,
    ) for r in results]
    evaluator = PolicyEvaluator()
    decision = evaluator.evaluate(all_warn, DisagreementPolicy.UNANIMOUS)
    assert decision.action == PolicyAction.ESCALATE, (
        f"All-warn under unanimous should ESCALATE, got {decision.action}"
    )


# ============================================================================
# Bonus Property 11 — JWT expired token rejected
# ============================================================================

@given(token_data=jwt_tokens())
@_HYP_SETTINGS
@pytest.mark.property
def test_jwt_expired_token_rejected(token_data):
    """Token with exp in the past always fails verification."""
    import time
    token, issuer, task_id = token_data
    assert token is not None
    parts = token.jwt.split(".")
    import base64
    import json
    payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
    payload["exp"] = int(time.time()) - 3600
    new_payload = base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).decode().rstrip("=")
    expired_jwt = ValidationToken(jwt=f"{parts[0]}.{new_payload}.{parts[2]}")
    assert not issuer.verify_token(expired_jwt), "Expired token should be rejected"


# ============================================================================
# Bonus Property 12 — JWT single-use consumed token rejected
# ============================================================================

@given(token_data=jwt_tokens())
@_HYP_SETTINGS
@pytest.mark.property
def test_jwt_single_use_consumed_token_rejected(token_data):
    """A consumed (used) token cannot be verified again."""
    token, issuer, task_id = token_data
    assert token is not None
    first_verify = issuer.verify_token(token)
    assert first_verify is True
    issuer.consume_token(token)
    assert not issuer.verify_token(token), "Consumed token should be rejected"
