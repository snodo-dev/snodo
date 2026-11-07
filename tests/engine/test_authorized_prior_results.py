"""Tests for reuse eligibility of authorized prior validator warns."""

from cryptography.hazmat.primitives.asymmetric import rsa

from snodo.core.interfaces import ValidatorResult
from snodo.engine.authorized_prior_results import authorized_prior_results
from snodo.infrastructure.decisions import SigningDecisionRecordIssuer


def _issuer_and_decision(severity="warn"):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = SigningDecisionRecordIssuer(private_key)
    result = ValidatorResult(
        validator_id="security", severity=severity, justification="Finding"
    )
    record = issuer.issue_record("task", "security", result, "proceed", "Authorized")
    return issuer, [record.jwt]


def _prior_result(severity="warn"):
    return {
        "validator_id": "security",
        "severity": severity,
        "justification": "Finding",
    }


def test_authorized_unchanged_warn_is_returned():
    issuer, decisions = _issuer_and_decision()
    prior = _prior_result()

    assert authorized_prior_results(
        "task_fix_1",
        "same spec",
        ["security"],
        issuer,
        decisions,
        [prior],
        "same  spec",
    ) == {"security": prior}


def test_changed_spec_is_not_returned():
    issuer, decisions = _issuer_and_decision()
    assert (
        authorized_prior_results(
            "task",
            "new spec",
            ["security"],
            issuer,
            decisions,
            [_prior_result()],
            "old spec",
        )
        == {}
    )


def test_validator_without_record_is_not_returned():
    issuer, decisions = _issuer_and_decision()
    assert (
        authorized_prior_results(
            "task", "same", ["other"], issuer, decisions, [_prior_result()], "same"
        )
        == {}
    )


def test_prior_blocker_is_not_returned():
    issuer, decisions = _issuer_and_decision()
    assert (
        authorized_prior_results(
            "task",
            "same",
            ["security"],
            issuer,
            decisions,
            [_prior_result("blocker")],
            "same",
        )
        == {}
    )


def test_prior_error_is_not_returned():
    issuer, decisions = _issuer_and_decision()
    assert (
        authorized_prior_results(
            "task",
            "same",
            ["security"],
            issuer,
            decisions,
            [_prior_result("error")],
            "same",
        )
        == {}
    )


def test_missing_prior_result_is_not_returned():
    issuer, decisions = _issuer_and_decision()
    assert (
        authorized_prior_results(
            "task", "same", ["security"], issuer, decisions, [], "same"
        )
        == {}
    )
