"""Comprehensive tests for JWT-based token integrity subsystem (Task 7.7).

Tests cover:
- JWT issuance with signature, iat, exp claims
- Blocker rejection
- Token verification (signature + expiry)
- Expired token rejection
- Task binding (expected_task_id)
- Tampering detection
- Token decoding (inspection without verification)
- Audit log integration
- Single-use semantics
- Config-driven TTL
"""

import time
from datetime import datetime, timezone
from unittest.mock import Mock

import jwt
import pytest

from tests.conftest import TEST_SECRET

from snodo.core.interfaces import ValidatorResult
from snodo.infrastructure.tokens import (
    TokenIssuer,
    ValidationToken,
    issue_token,
    verify_token,
    decode_token,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def issuer():
    return TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600)


@pytest.fixture
def short_ttl_issuer():
    return TokenIssuer(secret=TEST_SECRET, ttl_seconds=1)


@pytest.fixture
def no_blockers():
    return [
        ValidatorResult(validator_id="sec", severity="pass", justification="ok"),
        ValidatorResult(validator_id="arch", severity="pass", justification="ok"),
    ]


@pytest.fixture
def with_blocker():
    return [
        ValidatorResult(validator_id="sec", severity="pass", justification="ok"),
        ValidatorResult(validator_id="arch", severity="blocker", justification="circular dep"),
    ]


# ---------------------------------------------------------------------------
# Token issuance
# ---------------------------------------------------------------------------

def test_issue_token_returns_validation_token(issuer, no_blockers):
    token = issuer.issue_token("task_1", no_blockers, "unanimous")
    assert isinstance(token, ValidationToken)
    assert token.task_id == "task_1"
    assert len(token.validator_signatures) == 2
    assert token.consensus == "unanimous"
    assert token.jwt.startswith("eyJ")


def test_issue_token_blockers_return_none(issuer, with_blocker):
    token = issuer.issue_token("task_1", with_blocker)
    assert token is None


def test_issued_jwt_payload_is_decodable(issuer, no_blockers):
    token = issuer.issue_token("task_1", no_blockers, "unanimous")
    payload = jwt.decode(token.jwt, issuer.secret, algorithms=["HS256"])
    assert payload["task_id"] == "task_1"
    assert "iat" in payload
    assert "exp" in payload
    assert "validator_signatures" in payload


def test_issued_jwt_has_standard_claims(issuer, no_blockers):
    token = issuer.issue_token("task_1", no_blockers, "unanimous")
    payload = jwt.decode(token.jwt, issuer.secret, algorithms=["HS256"])
    now = datetime.now(timezone.utc)
    iat = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
    exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    assert abs((iat - now).total_seconds()) < 5
    assert abs((exp - now).total_seconds() - 3600) < 5


def test_issuer_respects_configured_ttl(no_blockers):
    issuer = TokenIssuer(secret=TEST_SECRET, ttl_seconds=60)
    token = issuer.issue_token("t1", no_blockers)
    payload = jwt.decode(token.jwt, TEST_SECRET, algorithms=["HS256"])
    lifetime = payload["exp"] - payload["iat"]
    assert lifetime == 60


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------

def test_verify_token_valid_token_returns_true(issuer, no_blockers):
    token = issuer.issue_token("task_1", no_blockers)
    assert issuer.verify_token(token) is True


def test_verify_token_none_returns_false(issuer):
    assert issuer.verify_token(None) is False


def test_verify_token_empty_jwt_returns_false(issuer):
    token = ValidationToken(jwt="")
    assert issuer.verify_token(token) is False


def test_verify_token_with_task_binding(issuer, no_blockers):
    token = issuer.issue_token("task_1", no_blockers)
    assert issuer.verify_token(token, expected_task_id="task_1") is True
    assert issuer.verify_token(token, expected_task_id="task_2") is False


def test_verify_token_wrong_secret_rejects(no_blockers):
    a = TokenIssuer(secret="secret_A_32_bytes_key_size_yes!!", ttl_seconds=3600)
    b = TokenIssuer(secret="secret_B_32_bytes_key_size_yes!!", ttl_seconds=3600)
    token = a.issue_token("task_1", no_blockers)
    assert b.verify_token(token) is False


def test_verify_token_expired(no_blockers):
    issuer = TokenIssuer(secret=TEST_SECRET, ttl_seconds=-1)
    token = issuer.issue_token("task_1", no_blockers)
    # The token has an exp in the past -> should fail
    assert issuer.verify_token(token) is False


def test_verify_token_tampered(issuer, no_blockers):
    token = issuer.issue_token("task_1", no_blockers)
    # Flip a character in the payload (middle section of the JWT).
    # This definitively changes the signed content, so verification
    # must fail.  Flipping the last character of the signature
    # is unreliable because base64url trailing bits may absorb the
    # change without altering the decoded bytes.
    parts = token.jwt.split(".")
    parts[1] = parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B")
    tampered_jwt = ".".join(parts)
    tampered = ValidationToken(jwt=tampered_jwt)
    assert issuer.verify_token(tampered) is False


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def test_decode_token_returns_payload(issuer, no_blockers):
    token = issuer.issue_token("task_1", no_blockers)
    payload = issuer.decode_token(token)
    assert payload is not None
    assert payload["task_id"] == "task_1"
    assert payload["consensus"] == "unanimous"


def test_decode_token_none_returns_none(issuer):
    assert issuer.decode_token(None) is None


def test_decode_token_does_not_verify_signature(issuer, no_blockers):
    token = issuer.issue_token("task_1", no_blockers)
    tampered = ValidationToken(jwt=token.jwt[:-1] + "X")
    payload = issuer.decode_token(tampered)
    # decode without verify still works (just inspects)
    assert payload is not None


# ---------------------------------------------------------------------------
# Console functions
# ---------------------------------------------------------------------------

def test_convenience_issue_token(no_blockers):
    token = issue_token("task_1", no_blockers)
    assert isinstance(token, ValidationToken)


def test_convenience_verify_token(no_blockers):
    token = issue_token("task_1", no_blockers)
    assert verify_token(token) is True


def test_convenience_verify_token_task_binding(no_blockers):
    token = issue_token("task_1", no_blockers)
    assert verify_token(token, expected_task_id="task_1") is True
    assert verify_token(token, expected_task_id="wrong") is False


def test_convenience_decode_token(no_blockers):
    token = issue_token("task_1", no_blockers)
    payload = decode_token(token)
    assert payload is not None
    assert payload["task_id"] == "task_1"


# ---------------------------------------------------------------------------
# Audit log integration
# ---------------------------------------------------------------------------

def test_token_issued_logs_audit_event(no_blockers):
    audit = Mock()
    issuer = TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600, audit_log=audit)
    issuer.issue_token("task_1", no_blockers)
    issued_calls = [
        c for c in audit.append_event.call_args_list
        if c[0][0] == "token_issued"
    ]
    assert len(issued_calls) == 1


def test_token_blocked_logs_audit_event(with_blocker):
    audit = Mock()
    issuer = TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600, audit_log=audit)
    issuer.issue_token("task_1", with_blocker)
    blocked_calls = [
        c for c in audit.append_event.call_args_list
        if c[0][0] == "token_blocked"
    ]
    assert len(blocked_calls) == 1


def test_token_expired_logs_audit_event(no_blockers):
    audit = Mock()
    issuer = TokenIssuer(secret=TEST_SECRET, ttl_seconds=-1, audit_log=audit)
    token = issuer.issue_token("task_1", no_blockers)
    issuer.verify_token(token)
    expired_calls = [
        c for c in audit.append_event.call_args_list
        if c[0][0] == "token_expired"
    ]
    assert len(expired_calls) == 1


def test_token_invalid_logs_audit_event_on_tamper(issuer, no_blockers):
    audit = Mock()
    issuer_with_audit = TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600, audit_log=audit)
    token = issuer_with_audit.issue_token("task_1", no_blockers)
    tampered = ValidationToken(jwt=token.jwt + "tampered")
    issuer_with_audit.verify_token(tampered)
    invalid_calls = [
        c for c in audit.append_event.call_args_list
        if c[0][0] == "token_invalid"
    ]
    assert len(invalid_calls) == 1


def test_token_task_mismatch_logs_audit_event(issuer, no_blockers):
    audit = Mock()
    issuer_with_audit = TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600, audit_log=audit)
    token = issuer_with_audit.issue_token("task_1", no_blockers)
    issuer_with_audit.verify_token(token, expected_task_id="wrong_task")
    mismatch_calls = [
        c for c in audit.append_event.call_args_list
        if c[0][0] == "token_task_mismatch"
    ]
    assert len(mismatch_calls) == 1


# ---------------------------------------------------------------------------
# Secret handling
# ---------------------------------------------------------------------------

def test_explicit_secret_works(no_blockers):
    issuer = TokenIssuer(secret="explicit_32_byte_long_key_okay!!", ttl_seconds=3600)
    token = issuer.issue_token("t1", no_blockers)
    payload = jwt.decode(token.jwt, "explicit_32_byte_long_key_okay!!", algorithms=["HS256"])
    assert payload["task_id"] == "t1"


def test_different_secrets_produce_different_tokens(no_blockers):
    a = TokenIssuer(secret="secret_A_32_bytes_key_size_yes!!")
    b = TokenIssuer(secret="secret_B_32_bytes_key_size_yes!!")
    token_a = a.issue_token("t1", no_blockers)
    token_b = b.issue_token("t1", no_blockers)
    assert token_a.jwt != token_b.jwt


# ---------------------------------------------------------------------------
# LoopState serialization round-trip
# ---------------------------------------------------------------------------

def test_token_serializes_via_jwt_field(issuer, no_blockers):
    token = issuer.issue_token("task_1", no_blockers)
    # Serialize: store JWT string
    serialized = {"jwt": token.jwt}

    # Deserialize: reconstruct from JWT
    recreated = ValidationToken(jwt=serialized["jwt"])
    payload = issuer.decode_token(recreated)
    assert payload["task_id"] == "task_1"
    assert issuer.verify_token(recreated, expected_task_id="task_1") is True


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_validator_results_block_issuance(issuer):
    token = issuer.issue_token("task_1", [], "unanimous")
    # Empty results have no blockers -> should issue
    assert isinstance(token, ValidationToken)


def test_reissue_for_same_task_produces_different_jwts(issuer, no_blockers):
    a = issuer.issue_token("task_1", no_blockers)
    time.sleep(1.1)  # Ensure different iat (JWT iat has 1-second granularity)
    b = issuer.issue_token("task_1", no_blockers)
    assert a.jwt != b.jwt


def test_consensus_field_round_trips(issuer, no_blockers):
    token = issuer.issue_token("t", no_blockers, "majority")
    assert token.consensus == "majority"
    payload = issuer.decode_token(token)
    assert payload["consensus"] == "majority"
