"""Signed DecisionRecord subsystem (INV1 + HI-CTRL integrity).

FILE: snodo/infrastructure/decisions.py

A DecisionRecord is an unforgeable, audited, persistent credential minted
ONLY by a human CLI action (`snodo adjudicate`).  It is the human-side
analog of the validation token: HS256-signed JWT (same secret/infra as
tokens.py), but persistent (no short TTL) and scoped to a single
validator's concern on a single task.

INV3: DecisionRecords can ONLY adjudicate non-blocker severities (warn).
Issuing with adjudicated_severity="blocker" or "error" is rejected at
mint time.  At policy-layer consultation, the blocker HALT runs FIRST,
so no DecisionRecord can ever override a genuine blocker.
"""

import hashlib
import os
import secrets
from dataclasses import dataclass
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


@dataclass
class DecisionRecord:
    """JWT-backed human adjudication record.

    The JWT string is the authoritative wire format.
    Convenience fields are decoded from the JWT at construction time.
    """
    jwt: str
    task_ref: str = ""
    validator_id: str = ""
    adjudicated_severity: str = ""
    adjudicated_justification: str = ""
    decision: str = ""
    justification: str = ""
    resolved_by: str = ""
    issued_at: str = ""


class DecisionRecordIssuer:
    """Issues and verifies signed DecisionRecords.

    Records are HS256-signed JWTs using the SAME secret as TokenIssuer.
    Unlike validation tokens, DecisionRecords have NO expiry — they are
    durable human adjudications that persist across re-dispatch.
    """

    def __init__(
        self,
        secret: Optional[str] = None,
        audit_log: Any = None,
    ):
        self.secret = secret or os.environ.get("SNODO_TOKEN_SECRET") or secrets.token_hex(32)
        self._audit_log = audit_log

    # ------------------------------------------------------------------
    # Issuance
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
        """Mint a signed DecisionRecord for a validator concern.

        INV3: Rejects adjudication of blocker or error severities.
        Only "warn" (and "escalation" as a synonym) can be adjudicated.

        Args:
            task_ref: Task identifier
            validator_id: Which validator raised the concern
            validator_result: The original ValidatorResult (for concern text)
            decision: "proceed" or "halt"
            justification: Human's reason for the decision
            resolved_by: Human identifier

        Returns:
            Signed DecisionRecord

        Raises:
            DecisionInvalidSeverityError: If severity is blocker or error
        """
        severity = validator_result.severity.lower().strip()

        if severity in ("blocker", "error"):
            raise DecisionInvalidSeverityError(
                f"Cannot mint DecisionRecord for severity '{severity}'. "
                "Blockers and errors are non-overridable (INV3)."
            )

        if decision not in ("proceed", "halt"):
            raise DecisionError(
                f"Decision must be 'proceed' or 'halt', got {decision!r}"
            )

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

        jwt_str = jwt.encode(payload, self.secret, algorithm="HS256")

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

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify_record(
        self,
        record_jwt: str,
        expected_task_ref: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Verify a DecisionRecord's signature and optional task binding.

        Args:
            record_jwt: The JWT string to verify
            expected_task_ref: If provided, also verify the record was
                               issued for this specific task

        Returns:
            Decoded payload dict if valid, None otherwise
        """
        if not record_jwt:
            return None

        try:
            payload = jwt.decode(
                record_jwt,
                self.secret,
                algorithms=["HS256"],
                options={"require": ["iat", "task_ref", "validator_id", "decision"]},
            )
        except jwt.InvalidTokenError:
            self._log_event("decision_record_invalid", {
                "task_ref": expected_task_ref or "unknown",
                "reason": "signature or format invalid",
            })
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
        """Find a valid DecisionRecord matching a specific concern.

        Searches the list of stored record JWTs for one that:
        - Has a valid signature
        - Matches task_ref, validator_id, and severity
        - Has decision="proceed"

        Args:
            records_jwt: List of stored JWT strings from session
            task_ref: Task to match
            validator_id: Validator to match
            severity: Severity to match (must be non-blocker)

        Returns:
            Decoded payload if a matching valid record with decision="proceed"
            is found, None otherwise.
        """
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
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _record_id(jwt_str: str) -> str:
        """Short stable identifier for a DecisionRecord JWT."""
        return hashlib.sha256(jwt_str.encode()).hexdigest()[:16]

    def _log_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Log to injected audit log if available."""
        if self._audit_log is not None:
            self._audit_log.append_event(event_type, data)


# Default issuer (no audit_log, for convenience functions).
_default_issuer = DecisionRecordIssuer()


def issue_record(
    task_ref: str,
    validator_id: str,
    validator_result: ValidatorResult,
    decision: str,
    justification: str,
    resolved_by: str = "human",
) -> DecisionRecord:
    """Convenience: issue a signed DecisionRecord (default issuer)."""
    return _default_issuer.issue_record(
        task_ref, validator_id, validator_result,
        decision, justification, resolved_by,
    )


def verify_record(
    record_jwt: str,
    expected_task_ref: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Convenience: verify a DecisionRecord (default issuer)."""
    return _default_issuer.verify_record(record_jwt, expected_task_ref=expected_task_ref)


def decode_record(record_jwt: str) -> Optional[Dict[str, Any]]:
    """Convenience: decode a DecisionRecord payload (default issuer)."""
    return _default_issuer.decode_record(record_jwt)
