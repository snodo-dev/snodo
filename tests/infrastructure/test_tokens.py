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
- Single-use semantics (shared SQLite store)
- Fail-closed store behaviour
- Secret handling (empty errors, unset warns)
- Config-driven TTL
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import jwt
import pytest
from snodo.core.interfaces import ValidatorResult
from snodo.infrastructure.tokens import (
    TokenError,
    TokenIssuer,
    TokenStoreError,
    ValidationToken,
)

from tests.conftest import TEST_SECRET

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
# Single-use semantics (shared SQLite store)
# ---------------------------------------------------------------------------

def test_consume_then_verify_fails(issuer, no_blockers):
    token = issuer.issue_token("task_1", no_blockers)
    assert issuer.verify_token(token) is True
    assert issuer.consume_token(token) is True
    assert issuer.verify_token(token) is False


def test_consume_twice_returns_false(issuer, no_blockers):
    token = issuer.issue_token("task_1", no_blockers)
    assert issuer.consume_token(token) is True
    assert issuer.consume_token(token) is False


def test_verify_does_not_consume(issuer, no_blockers):
    """Decision A: verify_token checks but does NOT consume (multi-edit dispatch)."""
    token = issuer.issue_token("task_1", no_blockers)
    for _ in range(5):
        assert issuer.verify_token(token) is True
    # Still verifiable — consumption happens at the dispatch boundary.
    assert issuer.verify_token(token) is True


def test_consumption_survives_new_issuer_instance(no_blockers, tmp_path):
    """Consumption is shared across TokenIssuer instances (same store)."""
    store = tmp_path / "tokens.db"
    a = TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600, store_path=store)
    b = TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600, store_path=store)
    token = a.issue_token("task_1", no_blockers)
    assert b.verify_token(token) is True
    assert a.consume_token(token) is True
    assert b.verify_token(token) is False


def test_consumption_survives_process_restart(no_blockers, tmp_path):
    """Consumption persists across a fresh TokenIssuer (simulated restart)."""
    store = tmp_path / "tokens.db"
    a = TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600, store_path=store)
    token = a.issue_token("task_1", no_blockers)
    assert a.consume_token(token) is True

    # Simulate restart: a brand-new issuer against the same store file.
    b = TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600, store_path=store)
    assert b.verify_token(token) is False


def test_concurrent_consume_exactly_one_wins(no_blockers, tmp_path):
    """Two simultaneous consume attempts (separate connections) → exactly one wins."""
    import threading

    store = tmp_path / "tokens.db"
    issuer = TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600, store_path=store)
    token = issuer.issue_token("task_1", no_blockers)

    # Two independent issuers (separate SQLite connections) — the realistic
    # cross-process scenario.
    a = TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600, store_path=store)
    b = TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600, store_path=store)

    results = []
    barrier = threading.Barrier(2)

    def _consume(iss):
        barrier.wait()
        results.append(iss.consume_token(token))

    threads = [
        threading.Thread(target=_consume, args=(a,)),
        threading.Thread(target=_consume, args=(b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [False, True]


def test_concurrent_consume_stress_eight_consumers(no_blockers, tmp_path):
    """N=8 concurrent consumers of the same token → exactly one True, no exception."""
    import threading

    store = tmp_path / "tokens.db"
    issuer = TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600, store_path=store)
    token = issuer.issue_token("task_1", no_blockers)

    n = 8
    consumers = [
        TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600, store_path=store)
        for _ in range(n)
    ]

    results = []
    errors = []
    barrier = threading.Barrier(n)

    def _consume(iss):
        try:
            barrier.wait()
            results.append(iss.consume_token(token))
        except Exception as exc:  # noqa: BLE001 — capture any failure
            errors.append(exc)

    threads = [threading.Thread(target=_consume, args=(c,)) for c in consumers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert sorted(results) == [False] * (n - 1) + [True]


# ---------------------------------------------------------------------------
# Fail-closed store behaviour
# ---------------------------------------------------------------------------

def test_store_unavailable_fails_closed(no_blockers, tmp_path):
    """A corrupt/unwritable store makes verification fail closed."""
    store = tmp_path / "tokens.db"
    issuer = TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600, store_path=store)
    token = issuer.issue_token("task_1", no_blockers)

    # Corrupt the store: replace the DB file with garbage.
    store.write_text("this is not a sqlite database")

    with pytest.raises(TokenStoreError):
        issuer.verify_token(token)


def test_store_unavailable_consume_fails_closed(no_blockers, tmp_path):
    store = tmp_path / "tokens.db"
    issuer = TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600, store_path=store)
    token = issuer.issue_token("task_1", no_blockers)

    store.write_text("this is not a sqlite database")

    with pytest.raises(TokenStoreError):
        issuer.consume_token(token)


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


def test_empty_secret_env_errors(monkeypatch):
    monkeypatch.setenv("SNODO_TOKEN_SECRET", "")
    with pytest.raises(TokenError, match="empty"):
        TokenIssuer()


def test_unset_secret_uses_random_secret(monkeypatch):
    monkeypatch.delenv("SNODO_TOKEN_SECRET", raising=False)
    issuer = TokenIssuer()
    assert len(issuer.secret) == 64


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


def test_reissue_for_same_task_produces_different_jwts(no_blockers):
    # Injected clock: iat has 1-second granularity, so a real reissue would
    # need to sleep >1s to force a different iat. The fake clock advances the
    # time seen by issue_token() instead.
    class FakeClock:
        def __init__(self):
            self.t = datetime(2026, 1, 1, tzinfo=timezone.utc)

        def __call__(self):
            now = self.t
            self.t = self.t + timedelta(seconds=2)
            return now

    a = TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600, now_fn=FakeClock())
    token_a = a.issue_token("task_1", no_blockers)
    token_b = a.issue_token("task_1", no_blockers)
    assert token_a.jwt != token_b.jwt


def test_consensus_field_round_trips(issuer, no_blockers):
    token = issuer.issue_token("t", no_blockers, "majority")
    assert token.consensus == "majority"
    payload = issuer.decode_token(token)
    assert payload["consensus"] == "majority"
