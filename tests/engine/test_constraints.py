"""Branch-coverage tests for ConstraintEngine.evaluate() and _apply_failure().

Uses fake stubs — no real LLM or filesystem access.
"""

import pytest
from unittest.mock import MagicMock
from snodo.compiler.models import Constraint, Protocol, Severity
from snodo.engine.constraints import ConstraintEngine
from snodo.engine.loop import LoopState
from snodo.core.interfaces import Task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_constraint(cid, predicate="", severity=Severity.BLOCKER, params=None):
    return Constraint(
        constraint_id=cid,
        description=f"Test constraint {cid}",
        predicate=predicate,
        severity=severity,
        params=params or {},
    )


def _make_state(mode="default"):
    task = Task(id="t1", spec="do something")
    state = LoopState(task=task, current_mode=mode)
    return state


def _make_engine(global_constraints=None, mode_constraints=None):
    """Build a ConstraintEngine with a minimal Protocol stub."""
    protocol = MagicMock(spec=Protocol)
    protocol.global_constraints = global_constraints or []

    if mode_constraints is not None:
        mode_obj = MagicMock()
        mode_obj.constraints = mode_constraints
        protocol.get_mode.return_value = mode_obj
    else:
        protocol.get_mode.return_value = None

    registry = MagicMock()
    engine = ConstraintEngine(
        protocol=protocol,
        predicate_registry=registry,
        workspace_mcp=None,
        git_mcp=None,
    )
    return engine, registry


def _audit_stub():
    calls = []

    def fn(event_type, data):
        calls.append((event_type, data))

    fn.calls = calls
    return fn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_no_constraints_early_return():
    """No global or mode constraints → early return, constraints_passed True."""
    engine, _ = _make_engine(global_constraints=[], mode_constraints=[])
    state = _make_state()
    audit = _audit_stub()

    result = engine.evaluate(state, "governance", audit)

    assert result.constraints_passed is True
    assert result.constraint_violations == []
    assert not audit.calls


def test_global_and_mode_constraints_collected():
    """Global + mode constraints are both included in the evaluation pass."""
    g_constraint = _make_constraint("g1", predicate="always_pass")
    m_constraint = _make_constraint("m1", predicate="always_pass")

    engine, registry = _make_engine(
        global_constraints=[g_constraint],
        mode_constraints=[m_constraint],
    )

    # predicate that passes
    pred = MagicMock()
    pred.evaluate.return_value = MagicMock(passed=True, justification="ok", evidence={})
    registry.lookup.return_value = pred

    state = _make_state()
    audit = _audit_stub()

    result = engine.evaluate(state, "governance", audit)

    # lookup called for both constraints
    assert registry.lookup.call_count == 2
    assert result.constraints_passed is True
    assert result.constraint_violations == []


def test_empty_predicate_skipped():
    """Constraint with empty predicate string is skipped (no lookup, no audit)."""
    c = _make_constraint("c1", predicate="")
    engine, registry = _make_engine(global_constraints=[c])

    state = _make_state()
    audit = _audit_stub()

    result = engine.evaluate(state, "governance", audit)

    registry.lookup.assert_not_called()
    assert result.constraints_passed is True
    assert result.constraint_violations == []
    assert not audit.calls


def test_unknown_predicate_emits_audit_and_blocks():
    """KeyError from registry.lookup → audit 'constraint_predicate_unknown' + blocker applied."""
    c = _make_constraint("c_unknown", predicate="no_such_pred")
    engine, registry = _make_engine(global_constraints=[c])
    registry.lookup.side_effect = KeyError("no_such_pred")

    state = _make_state()
    audit = _audit_stub()

    result = engine.evaluate(state, "governance", audit)

    assert len(audit.calls) == 1
    event_type, data = audit.calls[0]
    assert event_type == "constraint_predicate_unknown"
    assert data["constraint_id"] == "c_unknown"
    assert data["predicate_name"] == "no_such_pred"
    assert data["phase"] == "governance"

    assert result.is_blocked is True
    assert result.halt_type == "constraint"
    assert result.constraints_passed is False
    assert any("c_unknown" in v for v in result.constraint_violations)


def test_predicate_evaluate_exception_treated_as_failure():
    """Exception raised by pred.evaluate → result.passed False, justification contains error text."""
    c = _make_constraint("c_err", predicate="exploding_pred")
    engine, registry = _make_engine(global_constraints=[c])

    pred = MagicMock()
    pred.evaluate.side_effect = RuntimeError("boom")
    registry.lookup.return_value = pred

    state = _make_state()
    audit = _audit_stub()

    result = engine.evaluate(state, "governance", audit)

    # Failure applied — blocker by default
    assert result.is_blocked is True
    assert result.constraints_passed is False
    assert any("c_err" in v for v in result.constraint_violations)

    # audit emitted for the violation
    assert len(audit.calls) == 1
    event_type, data = audit.calls[0]
    assert event_type == "constraint_violation"
    assert "Predicate evaluation error" in data["justification"]


def test_predicate_passes_no_violation():
    """Passing predicate → no violation, constraints_passed stays True, no audit."""
    c = _make_constraint("c_pass", predicate="passing_pred")
    engine, registry = _make_engine(global_constraints=[c])

    pred = MagicMock()
    pred.evaluate.return_value = MagicMock(passed=True, justification="all good", evidence={})
    registry.lookup.return_value = pred

    state = _make_state()
    audit = _audit_stub()

    result = engine.evaluate(state, "governance", audit)

    assert result.constraints_passed is True
    assert result.constraint_violations == []
    assert not audit.calls


def test_predicate_fails_blocker_blocks_state():
    """Failing predicate with severity blocker → is_blocked True, halt_type constraint."""
    c = _make_constraint("c_block", predicate="blocking_pred", severity=Severity.BLOCKER)
    engine, registry = _make_engine(global_constraints=[c])

    pred = MagicMock()
    pred.evaluate.return_value = MagicMock(
        passed=False,
        justification="violation found",
        evidence={"detail": "bad"},
    )
    registry.lookup.return_value = pred

    state = _make_state()
    audit = _audit_stub()

    result = engine.evaluate(state, "governance", audit)

    assert result.is_blocked is True
    assert result.halt_type == "constraint"
    assert result.constraints_passed is False
    assert any("c_block" in v for v in result.constraint_violations)

    assert len(audit.calls) == 1
    event_type, data = audit.calls[0]
    assert event_type == "constraint_violation"
    assert data["severity"] == "blocker"
    assert data["constraint_id"] == "c_block"
    assert data["evidence"] == {"detail": "bad"}


def test_predicate_fails_warn_no_block():
    """Failing predicate with severity warn → violation appended but is_blocked stays False."""
    c = _make_constraint("c_warn", predicate="warn_pred", severity=Severity.WARN)
    engine, registry = _make_engine(global_constraints=[c])

    pred = MagicMock()
    pred.evaluate.return_value = MagicMock(
        passed=False,
        justification="just a warning",
        evidence={},
    )
    registry.lookup.return_value = pred

    state = _make_state()
    audit = _audit_stub()

    result = engine.evaluate(state, "governance", audit)

    assert result.is_blocked is False
    assert result.halt_type is None
    assert result.constraints_passed is True  # only blocker sets this False
    assert any("c_warn" in v for v in result.constraint_violations)

    assert len(audit.calls) == 1
    event_type, data = audit.calls[0]
    assert event_type == "constraint_violation"
    assert data["severity"] == "warn"


def test_apply_failure_blocker_sets_all_fields():
    """Direct unit test of _apply_failure with blocker severity."""
    state = _make_state()
    state.constraints_passed = True
    state.constraint_violations = []

    c = _make_constraint("c1", predicate="p")
    ConstraintEngine._apply_failure(state, c, "blocker", "reason A")

    assert state.is_blocked is True
    assert state.halt_type == "constraint"
    assert state.constraints_passed is False
    assert "c1: reason A" in state.constraint_violations


def test_apply_failure_non_blocker_no_block():
    """Direct unit test of _apply_failure with non-blocker severity."""
    state = _make_state()
    state.constraints_passed = True
    state.constraint_violations = []

    c = _make_constraint("c2", predicate="p")
    ConstraintEngine._apply_failure(state, c, "warn", "minor issue")

    assert state.is_blocked is False
    assert state.halt_type is None
    assert state.constraints_passed is True
    assert "c2: minor issue" in state.constraint_violations
