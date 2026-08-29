"""The halt hint names only the fix targets that apply to the halt in hand.

FILE: tests/engine/test_halt_hint.py (Fixes #38)

A blocker has three fix targets — code, spec, or policy.  A hint that names
one target for every blocker, or all three every time, is no better than a
hint that names nothing.  These tests assert the hint is derived from the
halt: its type, phase, and the blocking validators' cited criteria.
"""

from snodo.core.interfaces import ValidatorResult
from snodo.engine.nodes.writeback import (
    _blocker_fix_targets,
    _build_blocker_hint,
    _build_hint,
)


def _result(validator_id="architecture", severity="blocker", cited=None):
    return ValidatorResult(
        validator_id=validator_id,
        severity=severity,
        justification="criterion 1 is not met",
        cited_criteria=cited,
    )


class TestBlockerFixTargets:
    def test_constraint_is_policy(self):
        assert _blocker_fix_targets("constraint", "pre_execute", None) == ["policy"]

    def test_wf3_is_policy(self):
        assert _blocker_fix_targets("wf3", "pre_execute", None) == ["policy"]

    def test_max_iterations_is_spec_or_policy(self):
        assert _blocker_fix_targets("max_iterations", "unknown", None) == ["spec", "policy"]

    def test_recovery_exhausted_is_spec_or_policy(self):
        assert _blocker_fix_targets("recovery_exhausted", "unknown", None) == ["spec", "policy"]

    def test_recovery_stalled_is_spec_or_policy(self):
        assert _blocker_fix_targets("recovery_stalled", "unknown", None) == ["spec", "policy"]

    def test_post_execute_is_code(self):
        assert _blocker_fix_targets("blocked", "post_execute", None) == ["code"]

    def test_pre_execute_is_spec(self):
        assert _blocker_fix_targets("blocked", "pre_execute", None) == ["spec"]

    def test_pre_execute_cited_criterion_is_policy(self):
        results = [_result(cited=["[Criterion 1] Must not contradict an ADR"])]
        assert _blocker_fix_targets("blocked", "pre_execute", results) == ["policy"]

    def test_hint_does_not_list_all_three_every_time(self):
        """A post-execute blocker names only code, not spec or policy."""
        hint = _build_blocker_hint("blocked", "post_execute", None)
        assert "Fix the produced code" in hint
        assert "Revise the task spec" not in hint
        assert "protocol.yml" not in hint


class TestBlockerHintContent:
    def test_constraint_hint_names_policy_only(self):
        hint = _build_hint("blocker", "constraint", "pre_execute", None)
        assert "protocol.yml" in hint
        assert "Revise the task spec" not in hint
        assert "Fix the produced code" not in hint

    def test_post_execute_hint_names_code_only(self):
        hint = _build_hint("blocker", "blocked", "post_execute", None)
        assert "Fix the produced code" in hint
        assert "Revise the task spec" not in hint
        assert "protocol.yml" not in hint

    def test_pre_execute_hint_names_spec_only(self):
        hint = _build_hint("blocker", "blocked", "pre_execute", None)
        assert "Revise the task spec" in hint
        assert "Fix the produced code" not in hint
        assert "protocol.yml" not in hint

    def test_cited_criterion_hint_names_the_criterion(self):
        results = [_result("architecture", cited=["[Criterion 1] Must not contradict an ADR"])]
        hint = _build_hint("blocker", "blocked", "pre_execute", results)
        assert "based on a criterion" in hint
        assert "[Criterion 1] Must not contradict an ADR" in hint
        assert "protocol.yml is a legitimate place to fix" in hint

    def test_multiple_targets_join_with_or(self):
        hint = _build_hint("blocker", "max_iterations", "unknown", None)
        assert "Revise the task spec" in hint
        assert "protocol.yml" in hint
        assert " or " in hint

    def test_escalate_hint_unchanged(self):
        hint = _build_hint("escalate")
        assert "snodo authorize" in hint

    def test_internal_error_hint_unchanged(self):
        hint = _build_hint("internal_error")
        assert "internal" in hint
        assert "protocol.yml" not in hint
