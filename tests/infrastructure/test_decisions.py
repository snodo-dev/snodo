"""Tests for DecisionRecord mechanism (INV1 + INV3 + INV4 + HI-CTRL integrity).

FILE: tests/infrastructure/test_decisions.py

Acceptance criteria:
- DecisionRecord issue/verify: signature valid, tamper detected, task binding enforced
- Issuing with adjudicated_severity="blocker"/"error" is rejected
- Policy: warn + valid decision(proceed) → resolved
- Policy: warn without decision → ESCALATE under unanimous
- Policy: blocker + decision(proceed) → still HALT (INV3)
- Policy: error + decision → still HALT
- Audit: decision_record_issued events appear
- Human-gated: no MCP tool for autonomous minting
"""

import hashlib
import os
import pytest
from unittest.mock import MagicMock, patch

from snodo.infrastructure.decisions import (
    DecisionRecord,
    DecisionRecordIssuer,
    DecisionError,
    DecisionInvalidSeverityError,
)
from snodo.core.interfaces import ValidatorResult


# === Fixture helpers ===

def _make_result(validator_id: str, severity: str, justification: str = "") -> ValidatorResult:
    return ValidatorResult(
        validator_id=validator_id,
        severity=severity,
        justification=justification or f"{severity} from {validator_id}",
    )


def _make_issuer(audit_log=None) -> DecisionRecordIssuer:
    return DecisionRecordIssuer(secret="test-secret-key", audit_log=audit_log)


# === Issue / Verify Tests ===

class TestDecisionRecordIssueVerify:
    """DecisionRecord issue, verify, tamper detection, task binding."""

    def test_issue_returns_signed_record(self):
        issuer = _make_issuer()
        result = _make_result("security", "warn", "Missing input validation")
        record = issuer.issue_record(
            task_ref="t1",
            validator_id="security",
            validator_result=result,
            decision="proceed",
            justification="Acceptable risk for MVP",
        )
        assert record.jwt is not None
        assert record.task_ref == "t1"
        assert record.validator_id == "security"
        assert record.adjudicated_severity == "warn"
        assert record.decision == "proceed"
        assert record.justification == "Acceptable risk for MVP"

    def test_verify_valid_record(self):
        issuer = _make_issuer()
        result = _make_result("security", "warn")
        record = issuer.issue_record("t1", "security", result, "proceed", "OK")
        payload = issuer.verify_record(record.jwt, expected_task_ref="t1")
        assert payload is not None
        assert payload["task_ref"] == "t1"
        assert payload["validator_id"] == "security"
        assert payload["decision"] == "proceed"

    def test_verify_tampered_record_fails(self):
        issuer = _make_issuer()
        result = _make_result("security", "warn")
        record = issuer.issue_record("t1", "security", result, "proceed", "OK")
        # Tamper with the JWT
        tampered = record.jwt[:-5] + "XXXXX"
        payload = issuer.verify_record(tampered, expected_task_ref="t1")
        assert payload is None

    def test_verify_task_binding_enforced(self):
        issuer = _make_issuer()
        result = _make_result("security", "warn")
        record = issuer.issue_record("t1", "security", result, "proceed", "OK")
        # Verify with wrong task_ref
        payload = issuer.verify_record(record.jwt, expected_task_ref="t2")
        assert payload is None

    def test_verify_without_task_binding(self):
        issuer = _make_issuer()
        result = _make_result("security", "warn")
        record = issuer.issue_record("t1", "security", result, "proceed", "OK")
        payload = issuer.verify_record(record.jwt)
        assert payload is not None

    def test_decode_without_verification(self):
        issuer = _make_issuer()
        result = _make_result("security", "warn")
        record = issuer.issue_record("t1", "security", result, "proceed", "OK")
        payload = issuer.decode_record(record.jwt)
        assert payload is not None
        assert payload["task_ref"] == "t1"

    def test_empty_jwt_returns_none(self):
        issuer = _make_issuer()
        assert issuer.verify_record("") is None
        assert issuer.verify_record(None) is None
        assert issuer.decode_record("") is None


# === INV3: Blocker/Error Rejection Tests ===

class TestDecisionRecordINV3:
    """Cannot mint DecisionRecord for blocker or error severity."""

    def test_reject_blocker_severity(self):
        issuer = _make_issuer()
        result = _make_result("security", "blocker", "SQL injection vulnerability")
        with pytest.raises(DecisionInvalidSeverityError) as exc_info:
            issuer.issue_record("t1", "security", result, "proceed", "Accept it")
        assert "blocker" in str(exc_info.value).lower()
        assert "non-overridable" in str(exc_info.value).lower()

    def test_reject_error_severity(self):
        issuer = _make_issuer()
        result = _make_result("llm_security", "error", "LLM failed to produce verdict")
        with pytest.raises(DecisionInvalidSeverityError) as exc_info:
            issuer.issue_record("t1", "llm_security", result, "proceed", "Retry")
        assert "error" in str(exc_info.value).lower()

    def test_invalid_decision_rejected(self):
        issuer = _make_issuer()
        result = _make_result("security", "warn")
        with pytest.raises(DecisionError):
            issuer.issue_record("t1", "security", result, "ignore", "Bad decision")


# === Audit Tests ===

class TestDecisionRecordAudit:
    """DecisionRecord issuance and verification are audited."""

    def test_issue_logs_audit_event(self):
        audit_log = MagicMock()
        issuer = _make_issuer(audit_log=audit_log)
        result = _make_result("security", "warn")
        issuer.issue_record("t1", "security", result, "proceed", "OK")
        audit_log.append_event.assert_called_once()
        call_args = audit_log.append_event.call_args
        assert call_args[0][0] == "decision_record_issued"
        data = call_args[0][1]
        assert data["op"] == "decision_record_issued"
        assert data["task_ref"] == "t1"
        assert data["validator_id"] == "security"
        assert data["decision"] == "proceed"
        assert "record_id" in data

    def test_invalid_record_logs_audit_event(self):
        audit_log = MagicMock()
        issuer = _make_issuer(audit_log=audit_log)
        issuer.verify_record("invalid.jwt.token", expected_task_ref="t1")
        audit_log.append_event.assert_called_once()
        call_args = audit_log.append_event.call_args
        assert call_args[0][0] == "decision_record_invalid"


# === Policy Integration Tests ===

class TestPolicyDecisionRecordConsultation:
    """Policy evaluator consults DecisionRecords after blocker HALT."""

    def _make_evaluator(self, issuer=None):
        from snodo.engine.policy import PolicyEvaluator
        from snodo.compiler.models import DisagreementPolicy
        return PolicyEvaluator(decision_issuer=issuer), DisagreementPolicy.UNANIMOUS

    def test_warn_without_decision_escalates(self):
        issuer = _make_issuer()
        evaluator, policy = self._make_evaluator(issuer)
        results = [
            _make_result("security", "pass"),
            _make_result("architecture", "warn", "Minor concern"),
        ]
        decision = evaluator.evaluate(results, policy, decision_records=[], task_ref="t1")
        assert decision.action.value == "escalate"
        assert decision.warn_count == 1

    def test_warn_with_valid_decision_proceeds(self):
        issuer = _make_issuer()
        evaluator, policy = self._make_evaluator(issuer)
        warn_result = _make_result("architecture", "warn", "Minor concern")
        record = issuer.issue_record("t1", "architecture", warn_result, "proceed", "Acceptable")

        results = [
            _make_result("security", "pass"),
            warn_result,
        ]
        decision = evaluator.evaluate(
            results, policy,
            decision_records=[record.jwt],
            task_ref="t1",
        )
        assert decision.action.value == "proceed"
        assert decision.warn_count == 0
        assert decision.pass_count == 2

    def test_blocker_with_decision_still_halts(self):
        """INV3: DecisionRecord cannot override a blocker."""
        issuer = _make_issuer()
        evaluator, policy = self._make_evaluator(issuer)

        # We can't mint a record for a blocker, but even if someone
        # injected a forged JWT, the blocker HALT runs FIRST.
        results = [
            _make_result("security", "blocker", "Critical vulnerability"),
            _make_result("architecture", "pass"),
        ]
        decision = evaluator.evaluate(
            results, policy,
            decision_records=["some.forged.jwt"],
            task_ref="t1",
        )
        assert decision.action.value == "halt"
        assert decision.blocker_count == 1

    def test_error_with_decision_still_halts(self):
        """Error HALT runs before DecisionRecord consultation."""
        issuer = _make_issuer()
        evaluator, policy = self._make_evaluator(issuer)
        results = [
            _make_result("llm_security", "error", "LLM failed"),
            _make_result("architecture", "pass"),
        ]
        decision = evaluator.evaluate(
            results, policy,
            decision_records=["some.jwt"],
            task_ref="t1",
        )
        assert decision.action.value == "halt"

    def test_multiple_warns_partial_adjudication(self):
        """Only adjudicated warns are reclassified."""
        issuer = _make_issuer()
        evaluator, policy = self._make_evaluator(issuer)

        warn1 = _make_result("architecture", "warn", "Concern A")
        warn2 = _make_result("security", "warn", "Concern B")
        record1 = issuer.issue_record("t1", "architecture", warn1, "proceed", "OK")

        results = [
            _make_result("performance", "pass"),
            warn1,
            warn2,
        ]
        decision = evaluator.evaluate(
            results, policy,
            decision_records=[record1.jwt],
            task_ref="t1",
        )
        # warn1 adjudicated → pass, warn2 not → still warn
        assert decision.pass_count == 2
        assert decision.warn_count == 1
        assert decision.action.value == "escalate"  # not unanimous (1 warn remains)

    def test_decision_for_wrong_task_does_not_match(self):
        """DecisionRecord for task t2 should not resolve t1's warn."""
        issuer = _make_issuer()
        evaluator, policy = self._make_evaluator(issuer)
        warn_result = _make_result("architecture", "warn", "Concern")
        record = issuer.issue_record("t2", "architecture", warn_result, "proceed", "OK")

        results = [
            _make_result("security", "pass"),
            warn_result,
        ]
        decision = evaluator.evaluate(
            results, policy,
            decision_records=[record.jwt],
            task_ref="t1",  # Different task
        )
        assert decision.action.value == "escalate"
        assert decision.warn_count == 1

    def test_decision_for_wrong_validator_does_not_match(self):
        """DecisionRecord for validator A should not resolve validator B's warn."""
        issuer = _make_issuer()
        evaluator, policy = self._make_evaluator(issuer)
        warn_a = _make_result("architecture", "warn", "Concern A")
        record = issuer.issue_record("t1", "architecture", warn_a, "proceed", "OK")

        warn_b = _make_result("security", "warn", "Concern B")
        results = [
            _make_result("performance", "pass"),
            warn_b,
        ]
        decision = evaluator.evaluate(
            results, policy,
            decision_records=[record.jwt],
            task_ref="t1",
        )
        assert decision.action.value == "escalate"
        assert decision.warn_count == 1

    def test_decision_halt_does_not_reclassify(self):
        """A DecisionRecord with decision=halt should NOT reclassify the warn."""
        issuer = _make_issuer()
        evaluator, policy = self._make_evaluator(issuer)
        warn_result = _make_result("architecture", "warn", "Concern")
        record = issuer.issue_record("t1", "architecture", warn_result, "halt", "Too risky")

        results = [
            _make_result("security", "pass"),
            warn_result,
        ]
        decision = evaluator.evaluate(
            results, policy,
            decision_records=[record.jwt],
            task_ref="t1",
        )
        # decision=halt does NOT reclassify → still a warn
        assert decision.warn_count == 1
        assert decision.action.value == "escalate"


# === find_adjudicated bulk helper Tests ===

class TestFindAdjudicated:
    """DecisionRecordIssuer.find_adjudicated bulk matching."""

    def test_finds_matching_record(self):
        issuer = _make_issuer()
        result = _make_result("security", "warn", "Input validation")
        record = issuer.issue_record("t1", "security", result, "proceed", "OK")

        found = issuer.find_adjudicated([record.jwt], "t1", "security", "warn")
        assert found is not None
        assert found["decision"] == "proceed"

    def test_no_match_returns_none(self):
        issuer = _make_issuer()
        result = _make_result("security", "warn", "Input validation")
        record = issuer.issue_record("t1", "security", result, "proceed", "OK")

        found = issuer.find_adjudicated([record.jwt], "t1", "architecture", "warn")
        assert found is None

    def test_empty_list_returns_none(self):
        issuer = _make_issuer()
        found = issuer.find_adjudicated([], "t1", "security", "warn")
        assert found is None

    def test_invalid_jwt_ignored(self):
        issuer = _make_issuer()
        found = issuer.find_adjudicated(["invalid.jwt"], "t1", "security", "warn")
        assert found is None


# === Record ID Tests ===

class TestRecordID:
    """Stable record identifier."""

    def test_record_id_is_stable(self):
        issuer = _make_issuer()
        result = _make_result("security", "warn")
        record = issuer.issue_record("t1", "security", result, "proceed", "OK")
        record_id = issuer._record_id(record.jwt)
        assert len(record_id) == 16
        # Same JWT → same ID
        assert issuer._record_id(record.jwt) == record_id


# === INV3 Regression: resolution_override removed ===

class TestINV3Regression:
    """The old severity-blind resolution_override path is gone."""

    def test_loop_state_has_no_resolution_override(self):
        from snodo.engine.loop import LoopState, LoopStage
        from snodo.core.interfaces import Task

        task = Task(id="t1", spec="test")
        state = LoopState(task=task, current_mode="dev")
        assert not hasattr(state, "resolution_override")

    def test_policy_blocker_before_decision_consultation(self):
        """Verify the order: blocker HALT runs before any DecisionRecord logic."""
        from snodo.engine.policy import PolicyEvaluator, PolicyAction
        from snodo.compiler.models import DisagreementPolicy
        from snodo.infrastructure.decisions import DecisionRecordIssuer

        issuer = DecisionRecordIssuer(secret="test-secret")
        evaluator = PolicyEvaluator(decision_issuer=issuer)
        policy = DisagreementPolicy.UNANIMOUS

        # Blocker present → HALT immediately, no decision_records consulted
        results = [
            ValidatorResult(validator_id="sec", severity="blocker", justification="XSS"),
            ValidatorResult(validator_id="arch", severity="pass", justification="OK"),
        ]
        decision = evaluator.evaluate(
            results, policy,
            decision_records=["fake.jwt"],
            task_ref="t1",
        )
        assert decision.action == PolicyAction.HALT
        assert decision.blocker_count == 1
