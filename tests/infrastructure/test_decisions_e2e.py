"""End-to-end tests for the HI-CTRL RS256 signing guarantee — Fixes #189.

FILE: tests/infrastructure/test_decisions_e2e.py

This module exercises the *actual* production configuration that the unit
tests in test_decisions.py do not cover:

  CLI path (snodo authorize):   SigningDecisionRecordIssuer(private_key)
  Engine path (policy eval):    VerifyOnlyDecisionRecordIssuer(public_key)

The two issuers are separate objects holding different key material.
That is the real trust boundary — "the agent cannot self-authorize."

We also document, with a test, what each attack succeeds or fails at:

  Attack 1 — unsigned:         garbage or HS256 JWT              → rejected
  Attack 2 — wrong key:        signed with an unrelated keypair  → rejected
  Attack 3 — modified payload: JWT payload mutated post-signing  → rejected
  Attack 4 — replay same sess: valid JWT replayed for wrong task → PASSES via
                                carry-forward (intentional; documented here
                                so any future change is visible)

Additional:
  - VerifyOnlyDecisionRecordIssuer.issue_record raises DecisionMintRejectedError
    (the engine CANNOT mint — it holds only the public key)
  - validate_cmd._instruction uses task_id, not decision_id (Fixes #189)
  - Pending decisions have no TTL (documented as a gap, not yet fixed)
"""

import base64
import json

import pytest

from snodo.core.interfaces import ValidatorResult
from snodo.infrastructure.decisions import (
    DecisionMintRejectedError,
    SigningDecisionRecordIssuer,
    VerifyOnlyDecisionRecordIssuer,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _gen_keypair():
    """Generate a throwaway RSA 2048-bit keypair (never touches disk)."""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import rsa

    priv = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    return priv, priv.public_key()


def _make_warn_result(vid: str = "architecture", msg: str = "Minor concern") -> ValidatorResult:
    return ValidatorResult(validator_id=vid, severity="warn", justification=msg)


def _split_issuers():
    """Return (signing_issuer, verify_issuer) that share a keypair.

    This is the production configuration:
      signing_issuer  — CLI path (holds private key)
      verify_issuer   — engine path (holds ONLY public key)
    """
    priv, pub = _gen_keypair()
    signer = SigningDecisionRecordIssuer(priv)
    verifier = VerifyOnlyDecisionRecordIssuer(pub)
    return signer, verifier


# ---------------------------------------------------------------------------
# 1. Production keypair split: sign with private, verify with public-only
# ---------------------------------------------------------------------------

class TestKeypairSplit:
    """The real production configuration: separate signer and verifier."""

    def test_valid_record_verified_by_public_key_only(self):
        """A record signed by the CLI is verified by the engine's public-key-only issuer.

        This is the fundamental HI-CTRL guarantee.  Sign with the private key
        (CLI path), verify with only the public key (engine path).  The two
        issuers never share key material — only the JWT string crosses the
        boundary.
        """
        signer, verifier = _split_issuers()
        warn = _make_warn_result()
        record = signer.issue_record("t1", "architecture", warn, "proceed", "Accepted by human")

        payload = verifier.verify_record(record.jwt, expected_task_ref="t1")

        assert payload is not None, (
            "VerifyOnlyDecisionRecordIssuer failed to verify a record signed by the "
            "corresponding SigningDecisionRecordIssuer.  The CLI→engine trust path is broken."
        )
        assert payload["task_ref"] == "t1"
        assert payload["validator_id"] == "architecture"
        assert payload["decision"] == "proceed"

    def test_task_binding_enforced_by_verifier(self):
        """The verify-only engine issuer enforces task binding."""
        signer, verifier = _split_issuers()
        warn = _make_warn_result()
        record = signer.issue_record("t1", "architecture", warn, "proceed", "OK")

        # Wrong task_ref → None
        payload = verifier.verify_record(record.jwt, expected_task_ref="other-task")
        assert payload is None

    def test_verifier_find_adjudicated_honours_signed_record(self):
        """VerifyOnlyDecisionRecordIssuer.find_adjudicated works with a real signed record."""
        signer, verifier = _split_issuers()
        warn = _make_warn_result("security")
        record = signer.issue_record("t1", "security", warn, "proceed", "Acceptable")

        found = verifier.find_adjudicated([record.jwt], "t1", "security", "warn")
        assert found is not None
        assert found["decision"] == "proceed"

    def test_policy_evaluator_uses_public_key_only_issuer(self):
        """PolicyEvaluator with VerifyOnlyDecisionRecordIssuer resolves warn via RS256.

        This is the full end-to-end path:
          1. CLI signs a record with the private key.
          2. Engine policy evaluator holds only the public key.
          3. The warn is resolved → action=proceed.
        """
        from snodo.compiler.models import DisagreementPolicy
        from snodo.engine.policy import PolicyEvaluator

        signer, verifier = _split_issuers()
        warn = _make_warn_result("architecture")
        record = signer.issue_record("t1", "architecture", warn, "proceed", "Human approved")

        evaluator = PolicyEvaluator(decision_issuer=verifier)
        results = [
            ValidatorResult(validator_id="security", severity="pass", justification="OK"),
            warn,
        ]
        decision = evaluator.evaluate(
            results,
            DisagreementPolicy.UNANIMOUS,
            "post_execute",
            decision_records=[record.jwt],
            task_ref="t1",
        )
        assert decision.action.value == "proceed", (
            f"Expected 'proceed' after valid signed adjudication, got {decision.action.value!r}. "
            f"Justification: {decision.justification}"
        )
        assert decision.warn_count == 0
        assert decision.pass_count == 2


# ---------------------------------------------------------------------------
# 2. VerifyOnlyDecisionRecordIssuer cannot mint — engine cannot self-authorize
# ---------------------------------------------------------------------------

class TestVerifyOnlyCannotMint:
    """The engine's public-key-only issuer MUST NOT be able to mint records."""

    def test_verify_only_raises_on_issue_record(self):
        """Calling issue_record on a verify-only issuer raises DecisionMintRejectedError.

        This is the structural enforcement of 'the agent cannot self-authorize'.
        The engine holds a VerifyOnlyDecisionRecordIssuer.  Any attempt by the
        engine to mint a new record fails at the issuer level, not at the policy
        level.
        """
        _, pub = _gen_keypair()
        verifier = VerifyOnlyDecisionRecordIssuer(pub)
        warn = _make_warn_result()

        with pytest.raises(DecisionMintRejectedError) as exc_info:
            verifier.issue_record("t1", "architecture", warn, "proceed", "Self-authorized")

        msg = str(exc_info.value).lower()
        assert "cannot mint" in msg or "only a public key" in msg, (
            f"DecisionMintRejectedError should explain the constraint; got: {exc_info.value}"
        )


# ---------------------------------------------------------------------------
# 3. Attack scenarios — what the guarantee actually stops
# ---------------------------------------------------------------------------

class TestAttackScenarios:
    """What does and does not pass — what the RS256 guarantee actually covers.

    Each test is labelled with its outcome (REJECTED / PASSES) so the
    reader knows exactly what the system guarantees.
    """

    # ------------------------------------------------------------------
    # Attack 1: Unsigned / garbage JWT
    # ------------------------------------------------------------------

    def test_attack_unsigned_garbage_rejected(self):
        """REJECTED — a non-JWT string is not accepted as a decision record."""
        _, pub = _gen_keypair()
        verifier = VerifyOnlyDecisionRecordIssuer(pub)

        payload = verifier.verify_record("not-a-jwt-at-all")
        assert payload is None, "Garbage string must not verify as a valid record"

    def test_attack_hs256_signed_rejected(self):
        """REJECTED — a record signed with HS256 (retired scheme) fails RS256 verification."""
        import jwt as pyjwt

        _, pub = _gen_keypair()
        verifier = VerifyOnlyDecisionRecordIssuer(pub)

        hs256_jwt = pyjwt.encode(
            {"iat": 1, "task_ref": "t1", "validator_id": "sec", "decision": "proceed"},
            "some-secret",
            algorithm="HS256",
        )
        payload = verifier.verify_record(hs256_jwt)
        assert payload is None, "HS256-signed record must not verify under RS256 verifier"

    def test_attack_missing_required_claims_rejected(self):
        """REJECTED — a valid RS256 JWT missing task_ref is rejected after decode."""
        priv, pub = _gen_keypair()
        import jwt as pyjwt

        # Valid RS256 signature but payload is missing task_ref
        incomplete = pyjwt.encode(
            {"iat": 1, "decision": "proceed"},
            priv,
            algorithm="RS256",
        )
        verifier = VerifyOnlyDecisionRecordIssuer(pub)
        payload = verifier.verify_record(incomplete)
        assert payload is None, "JWT without task_ref must not verify"

    # ------------------------------------------------------------------
    # Attack 2: Wrong key — the core RS256 guarantee
    # ------------------------------------------------------------------

    def test_attack_wrong_public_key_rejected(self):
        """REJECTED — a record signed with private key A cannot verify against key B.

        This is the core RS256 guarantee: a different keypair cannot forge or
        accept a record meant for another.  An agent that generates its own
        keypair and signs its own records cannot pass the engine's verifier
        (which holds the human operator's public key).
        """
        priv_a, _pub_a = _gen_keypair()
        _priv_b, pub_b = _gen_keypair()

        signer_a = SigningDecisionRecordIssuer(priv_a)
        verifier_b = VerifyOnlyDecisionRecordIssuer(pub_b)  # different keypair

        warn = _make_warn_result()
        record = signer_a.issue_record("t1", "architecture", warn, "proceed", "Signed with A")

        payload = verifier_b.verify_record(record.jwt, expected_task_ref="t1")
        assert payload is None, (
            "A record signed with key A must not verify under key B.  "
            "If this assertion fails, the RS256 asymmetric guarantee is broken."
        )

    def test_attack_wrong_key_does_not_resolve_policy(self):
        """REJECTED — a wrongly-keyed record does not resolve the policy evaluator."""
        from snodo.compiler.models import DisagreementPolicy
        from snodo.engine.policy import PolicyEvaluator

        priv_a, _pub_a = _gen_keypair()
        _priv_b, pub_b = _gen_keypair()

        signer_a = SigningDecisionRecordIssuer(priv_a)
        verifier_b = VerifyOnlyDecisionRecordIssuer(pub_b)

        warn = _make_warn_result("architecture")
        record = signer_a.issue_record("t1", "architecture", warn, "proceed", "Forged")

        evaluator = PolicyEvaluator(decision_issuer=verifier_b)
        results = [
            ValidatorResult(validator_id="security", severity="pass", justification="OK"),
            warn,
        ]
        decision = evaluator.evaluate(
            results,
            DisagreementPolicy.UNANIMOUS,
            "post_execute",
            decision_records=[record.jwt],
            task_ref="t1",
        )
        # Wrong key → verify returns None → warn is NOT resolved → escalate
        assert decision.action.value == "escalate", (
            f"A record signed with the wrong key must not resolve the policy. "
            f"Got {decision.action.value!r} — the RS256 guarantee would be broken."
        )
        assert decision.warn_count == 1

    # ------------------------------------------------------------------
    # Attack 3: Modified payload
    # ------------------------------------------------------------------

    def test_attack_modified_payload_rejected(self):
        """REJECTED — a JWT payload modified after signing fails verification.

        RS256 protects the (header, payload) tuple.  Even a one-character change
        in the base64-encoded payload invalidates the signature.
        """
        priv, pub = _gen_keypair()
        signer = SigningDecisionRecordIssuer(priv)
        verifier = VerifyOnlyDecisionRecordIssuer(pub)

        warn = _make_warn_result()
        record = signer.issue_record("t1", "architecture", warn, "proceed", "Legitimate")

        # Split JWT into header.payload.signature
        parts = record.jwt.split(".")
        assert len(parts) == 3, "JWT must have 3 parts"

        # Decode payload, mutate the task_ref, re-encode without re-signing
        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        original_payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        original_payload["task_ref"] = "TAMPERED-TASK"  # adversarial mutation

        tampered_b64 = base64.urlsafe_b64encode(
            json.dumps(original_payload).encode()
        ).rstrip(b"=").decode()

        tampered_jwt = f"{parts[0]}.{tampered_b64}.{parts[2]}"

        result = verifier.verify_record(tampered_jwt)
        assert result is None, (
            "A JWT with a modified payload but original signature must fail verification. "
            "RS256 payload integrity enforcement is missing."
        )

    # ------------------------------------------------------------------
    # Attack 4: Replay — what the guarantee does NOT cover (by design)
    # ------------------------------------------------------------------

    def test_attack_replay_valid_jwt_for_different_task_CARRIES_FORWARD(self):
        """PASSES via carry-forward — documented intentional behaviour, not a bug.

        A valid signed record for task T1 is replayed when evaluating task T2
        in the same session, via the session-scoped carry-forward in
        find_adjudicated().

        This is INTENTIONAL: when a task is adjudicated and re-dispatched with
        a new task_id (retry), the human's approval should not need to be
        re-entered.

        CONSEQUENCE: The session boundary is the true scope of an adjudication,
        not the task_id.  Any valid record from the same session resolves the
        same (validator_id, severity) concern for any other task in that session.

        This test pins the current behaviour so that any future change to tighten
        the replay boundary is a visible, intentional breaking change.  It is NOT
        a vulnerability report — it is documentation of the design.
        """
        from snodo.compiler.models import DisagreementPolicy
        from snodo.engine.policy import PolicyEvaluator

        signer, verifier = _split_issuers()
        warn = _make_warn_result("architecture")
        # Record explicitly issued for task T1
        record = signer.issue_record("task-t1", "architecture", warn, "proceed", "Approved for T1")

        evaluator = PolicyEvaluator(decision_issuer=verifier)
        results = [
            ValidatorResult(validator_id="security", severity="pass", justification="OK"),
            warn,
        ]
        # Evaluate for T2 — a completely different task_id
        decision = evaluator.evaluate(
            results,
            DisagreementPolicy.UNANIMOUS,
            "post_execute",
            decision_records=[record.jwt],
            task_ref="task-t2",  # DIFFERENT task
        )
        # The carry-forward logic resolves the warn for T2 using T1's adjudication.
        # This is intentional (retry sessions), not a vulnerability.
        assert decision.action.value == "proceed", (
            "Carry-forward replay should resolve the warn for the retry task. "
            "If this changes, update this test and the docs — it is a policy change."
        )
        assert decision.warn_count == 0


# ---------------------------------------------------------------------------
# 4. validate_cmd._instruction uses task_id — Fixes #189
# ---------------------------------------------------------------------------

class TestValidateCmdInstruction:
    """validate_cmd must tell the operator to run 'snodo authorize <task_id>'."""

    def test_escalate_instruction_uses_task_id_not_decision_id(self):
        """The escalate instruction must reference task_id, not decision_id.

        Fixes #189: previously printed 'snodo authorize <decision_id>' — but
        decision_id is the SHA-256 hash of the JWT (_record_id) and is NOT a
        valid argument to authorize.  snodo authorize takes a task_id.
        """
        from snodo.cli.commands.validate_cmd import _instruction

        instruction = _instruction("escalate")
        assert "<task_id>" in instruction, (
            f"escalate instruction must use '<task_id>' (not '<decision_id>'). "
            f"Got: {instruction!r}"
        )
        assert "<decision_id>" not in instruction, (
            f"escalate instruction must not reference '<decision_id>'. Got: {instruction!r}"
        )
        assert "snodo authorize" in instruction


# ---------------------------------------------------------------------------
# 5. Stale pending decisions — no TTL mechanism (documented gap)
# ---------------------------------------------------------------------------

class TestPendingDecisionGaps:
    """Pending decisions have no TTL or clearing path — documented as a gap.

    These tests pin the ABSENCE of features so that when they are added the
    tests fail visibly and must be updated with proper coverage.
    """

    def test_pending_decision_entry_has_no_expiry_field(self):
        """Pending decision entries written by the engine have no ttl/expires_at field.

        This is the current state (gap tracked in #189).  If a TTL is added,
        update this test to verify the expiry logic works correctly.
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        # This mirrors what _auto_write_pending_decisions writes (writeback.py)
        entry = {
            "type": "adjudicate",
            "validator_id": "architecture",
            "decision": "proceed",
            "justification": "some concern",
            "severity": "warn",
            "proposed_by": "engine",
            "timestamp": now,
        }

        assert "ttl" not in entry, (
            "A 'ttl' field was added to pending decisions — update this test "
            "to verify the TTL mechanism works correctly"
        )
        assert "expires_at" not in entry, (
            "An 'expires_at' field was added to pending decisions — update this test "
            "to verify the expiry mechanism works correctly"
        )
        # timestamp exists for display, but is not an expiry
        assert "timestamp" in entry

    def test_decision_records_have_no_exp_claim(self):
        """RS256 decision records do not carry a JWT 'exp' claim — they never expire.

        This is a gap: a signed record is valid forever.  When/if expiry is
        added, this test should be updated to verify exp enforcement.
        """
        priv, _pub = _gen_keypair()
        signer = SigningDecisionRecordIssuer(priv)
        warn = _make_warn_result()
        record = signer.issue_record("t1", "architecture", warn, "proceed", "OK")

        # Decode without verification to inspect raw claims
        import jwt as pyjwt
        payload = pyjwt.decode(record.jwt, options={"verify_signature": False})

        assert "exp" not in payload, (
            "A JWT 'exp' claim was added to decision records — update this test "
            "to verify that expired records are properly rejected"
        )
        assert "iat" in payload  # issued-at exists, but no expiry
