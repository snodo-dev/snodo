"""Signed DecisionRecord subsystem (INV1 + HI-CTRL integrity).

FILE: snodo/infrastructure/decisions.py

A DecisionRecord is an unforgeable, audited, persistent credential minted
ONLY by a human CLI action (`snodo adjudicate`).  It is the human-side
analog of the validation token: RS256-signed JWT (asymmetric — the CLI
holds the private key, the engine/MCP hold the public key).

SIGNING (CLI only):
    SigningDecisionRecordIssuer(private_key) — can sign + verify

VERIFY-ONLY (engine, MCP):
    VerifyOnlyDecisionRecordIssuer(public_key) — can verify, RAISES on
    any attempt to mint.  "The agent cannot self-authorize."

INV3: DecisionRecords can ONLY adjudicate non-blocker severities (warn).
Issuing with adjudicated_severity="blocker" or "error" is rejected at
mint time.

RS256 CLEAN BREAK: HS256 decision records are retired.  A stale HS256
record fails verification with a clear error message.  No dual-scheme
support.
"""

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import jwt

from snodo.core.interfaces import ValidatorResult


class DecisionError(Exception):
    """Base exception for DecisionRecord operations."""


class DecisionVerificationError(DecisionError):
    """DecisionRecord verification failed (signature or tampering)."""


class DecisionInvalidSeverityError(DecisionError):
    """Attempted to mint a DecisionRecord for a blocker or error severity."""


class DecisionMintRejectedError(DecisionError):
    """A verify-only issuer was asked to mint — not allowed."""


class DecisionRecord:
    """JWT-backed human adjudication record.

    The JWT string is the authoritative wire format.
    Convenience fields are decoded from the JWT at construction time.
    """

    def __init__(
        self,
        jwt: str,
        task_ref: str = "",
        validator_id: str = "",
        adjudicated_severity: str = "",
        adjudicated_justification: str = "",
        decision: str = "",
        justification: str = "",
        resolved_by: str = "",
        issued_at: str = "",
    ):
        self.jwt = jwt
        self.task_ref = task_ref
        self.validator_id = validator_id
        self.adjudicated_severity = adjudicated_severity
        self.adjudicated_justification = adjudicated_justification
        self.decision = decision
        self.justification = justification
        self.resolved_by = resolved_by
        self.issued_at = issued_at


class DecisionRecordIssuer:
    """Base class for RS256 DecisionRecord issuing and verification.

    Concrete subclasses:
        SigningDecisionRecordIssuer — holds private key, can mint
        VerifyOnlyDecisionRecordIssuer — holds public key, verify only
    """

    _ALGORITHM = "RS256"

    def __init__(self, audit_log: Any = None):
        self._audit_log = audit_log

    # ------------------------------------------------------------------
    # Issuance (overridden by signing subclass)
    # ------------------------------------------------------------------

    def issue_record(
        self,
        task_ref: str,
        validator_id: str,
        validator_result: ValidatorResult,
        decision: str,
        justification: str,
        resolved_by: str = "human",
    ) -> DecisionRecord:
        """Mint a signed DecisionRecord — signing subclass must override."""
        raise DecisionMintRejectedError(
            "This issuer holds only a public key and cannot mint "
            "DecisionRecords.  Only the CLI adjudicate path can mint."
        )

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify_record(
        self,
        record_jwt: str,
        expected_task_ref: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Verify a DecisionRecord's RS256 signature and optional task binding.

        Args:
            record_jwt: The JWT string to verify
            expected_task_ref: If provided, also verify the record was
                               issued for this specific task

        Returns:
            Decoded payload dict if valid, None otherwise.
        """
        if not record_jwt:
            return None

        try:
            payload = jwt.decode(
                record_jwt,
                self._verify_key(),
                algorithms=[self._ALGORITHM],
                options={"verify_signature": True},
            )
        except jwt.exceptions.InvalidAlgorithmError:
            # HS256 retirement — stale record from the old scheme
            self._log_event("decision_record_invalid", {
                "task_ref": expected_task_ref or "unknown",
                "reason": "decision record signed with retired HS256 scheme; re-adjudicate",
            })
            return None
        except jwt.InvalidTokenError:
            self._log_event("decision_record_invalid", {
                "task_ref": expected_task_ref or "unknown",
                "reason": "signature or format invalid",
            })
            return None

        if not payload.get("iat") or not payload.get("task_ref"):
            return None

        if expected_task_ref is not None and payload.get("task_ref") != expected_task_ref:
            self._log_event("decision_record_task_mismatch", {
                "task_ref": expected_task_ref,
                "record_task_ref": payload.get("task_ref"),
            })
            return None

        return payload

    def decode_record(self, record_jwt: str) -> Optional[Dict[str, Any]]:
        """Decode a DecisionRecord payload (no signature verification)."""
        if not record_jwt:
            return None
        try:
            return jwt.decode(record_jwt, options={"verify_signature": False})
        except jwt.InvalidTokenError:
            return None

    # ------------------------------------------------------------------
    # Bulk helpers for policy-layer consultation
    # ------------------------------------------------------------------

    def find_adjudicated(
        self,
        records_jwt: List[str],
        task_ref: str,
        validator_id: str,
        severity: str,
    ) -> Optional[Dict[str, Any]]:
        """Find a valid DecisionRecord matching a specific concern."""
        for r_jwt in records_jwt:
            payload = self.verify_record(r_jwt, expected_task_ref=task_ref)
            if payload is None:
                continue
            if (
                payload.get("validator_id") == validator_id
                and payload.get("adjudicated_severity") == severity
                and payload.get("decision") == "proceed"
            ):
                return payload
        return None

    # ------------------------------------------------------------------
    # Internals — subclasses must implement _verify_key
    # ------------------------------------------------------------------

    def _verify_key(self):
        """Return the key used for verification (public key)."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _record_id(jwt_str: str) -> str:
        """Short stable identifier for a DecisionRecord JWT."""
        return hashlib.sha256(jwt_str.encode()).hexdigest()[:16]

    @staticmethod
    def _validate_severity(severity: str) -> None:
        if severity in ("blocker", "error"):
            raise DecisionInvalidSeverityError(
                f"Cannot mint DecisionRecord for severity '{severity}'. "
                "Blockers and errors are non-overridable (INV3)."
            )

    @staticmethod
    def _validate_decision(decision: str) -> None:
        if decision not in ("proceed", "halt"):
            raise DecisionError(
                f"Decision must be 'proceed' or 'halt', got {decision!r}"
            )

    def _log_event(self, event_type: str, data: Dict[str, Any]) -> None:
        if self._audit_log is not None:
            self._audit_log.append_event(event_type, data)


class SigningDecisionRecordIssuer(DecisionRecordIssuer):
    """Issues RS256-signed DecisionRecords (CLI only — holds private key)."""

    def __init__(self, private_key, audit_log: Any = None):
        super().__init__(audit_log=audit_log)
        self._private_key = private_key

    def issue_record(
        self,
        task_ref: str,
        validator_id: str,
        validator_result: ValidatorResult,
        decision: str,
        justification: str,
        resolved_by: str = "human",
    ) -> DecisionRecord:
        """Mint an RS256-signed DecisionRecord."""
        severity = validator_result.severity.lower().strip()
        self._validate_severity(severity)
        self._validate_decision(decision)

        now = datetime.now(timezone.utc)

        payload = {
            "iat": now,
            "task_ref": task_ref,
            "validator_id": validator_id,
            "adjudicated_severity": severity,
            "adjudicated_justification": validator_result.justification,
            "decision": decision,
            "justification": justification,
            "resolved_by": resolved_by,
        }

        jwt_str = jwt.encode(payload, self._private_key, algorithm=self._ALGORITHM)

        record = DecisionRecord(
            jwt=jwt_str,
            task_ref=task_ref,
            validator_id=validator_id,
            adjudicated_severity=severity,
            adjudicated_justification=validator_result.justification,
            decision=decision,
            justification=justification,
            resolved_by=resolved_by,
            issued_at=now.isoformat(),
        )

        self._log_event("decision_record_issued", {
            "op": "decision_record_issued",
            "task_ref": task_ref,
            "validator_id": validator_id,
            "adjudicated_severity": severity,
            "decision": decision,
            "resolved_by": resolved_by,
            "record_id": self._record_id(jwt_str),
        })

        return record

    def _verify_key(self):
        return self._private_key.public_key()


class VerifyOnlyDecisionRecordIssuer(DecisionRecordIssuer):
    """Verifies RS256 DecisionRecords (engine/MCP — holds public key only).

    Any call to ``issue_record`` raises ``DecisionMintRejectedError`` —
    this issuer CANNOT mint.  Only the CLI adjudicate path (which loads
    the private key) can mint.
    """

    def __init__(self, public_key, audit_log: Any = None):
        super().__init__(audit_log=audit_log)
        self._public_key = public_key

    def _verify_key(self):
        return self._public_key


# ------------------------------------------------------------------#
# Compatibility aliases for construction from paths
# ------------------------------------------------------------------#

def signing_issuer(audit_log: Any = None) -> SigningDecisionRecordIssuer:
    """Create a signing issuer from the private key on disk (CLI only)."""
    from snodo.infrastructure.signing_keys import load_private_key
    return SigningDecisionRecordIssuer(load_private_key(), audit_log=audit_log)


def verify_only_issuer(audit_log: Any = None) -> VerifyOnlyDecisionRecordIssuer:
    """Create a verify-only issuer from the public key on disk."""
    from snodo.infrastructure.signing_keys import load_public_key
    return VerifyOnlyDecisionRecordIssuer(load_public_key(), audit_log=audit_log)
