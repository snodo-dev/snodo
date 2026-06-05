"""JWT-based token integrity subsystem (INV1).

FILE: snodo/infrastructure/tokens.py (Task 7.7)

Consolidated token model using PyJWT for standard signing, expiry,
and tamper detection. Replaces the previous two-class system
(interfaces.ValidationToken + tokens.ValidationToken) with a single
JWT-backed ValidationToken wrapper and a unified TokenIssuer.

Standard claims: iat (issued at), exp (expiry at)
Custom claims:  task_id, validator_signatures, consensus
"""

import hashlib
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import jwt

from snodo.core.interfaces import ValidatorResult


class TokenError(Exception):
    """Base exception for token operations."""


class TokenVerificationError(TokenError):
    """Token verification failed (signature, tampering)."""


class TokenExpiredError(TokenError):
    """Token has passed its TTL."""


class TokenTaskMismatchError(TokenError):
    """Token does not reference the expected task."""


class TokenIssuanceError(TokenError):
    """Token could not be issued."""


@dataclass
class ValidationToken:
    """JWT-backed validation credential.

    The JWT string is the authoritative wire format.
    Convenience fields are decoded from the JWT at construction time.
    LoopState stores this dataclass; LangGraph checkpoint stores the JWT string.
    """
    jwt: str
    task_id: str = ""
    validator_signatures: List[str] = field(default_factory=list)
    consensus: str = ""
    issued_at: str = ""
    expires_at: str = ""


class TokenIssuer:
    """Issues and verifies JWT validation tokens.

    Tokens are HS256-signed JWTs with standard iat/exp claims
    and custom claims for task_id, validator_signatures, and consensus.
    PyJWT handles signature verification and expiry automatically.
    """

    def __init__(
        self,
        secret: Optional[str] = None,
        ttl_seconds: int = 600,
        audit_log: Any = None,
    ):
        self.secret = secret or os.environ.get("SNODO_TOKEN_SECRET") or secrets.token_hex(32)
        self.ttl_seconds = ttl_seconds
        self._audit_log = audit_log
        self._used_tokens: set[str] = set()

    def issue_token(
        self,
        task_id: str,
        validator_results: List[ValidatorResult],
        consensus: str = "unanimous",
    ) -> Optional[ValidationToken]:
        """Issue a JWT validation token if no blockers present.

        INV3 root: a token can only be issued when the validator quorum
        is satisfied (no blocker results).  Without a token, mutating
        tools are gated by WF1.  This makes non-overridable validation
        structural — blockers prevent token issuance, and without a
        token the MCP server rejects all mutations.

        Args:
            task_id: Unique identifier for the task
            validator_results: Results from validator quorum
            consensus: Type of consensus achieved

        Returns:
            ValidationToken wrapper, or None if blockers present
        """
        if self._has_blockers(validator_results):
            blocker_ids = [
                r.validator_id for r in validator_results if r.severity == "blocker"
            ]
            self._log_event("token_blocked", {
                "task_ref": task_id,
                "blocker_validators": blocker_ids,
            })
            return None

        signatures = [
            f"{result.validator_id}:{result.severity}"
            for result in validator_results
        ]

        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=self.ttl_seconds)

        payload = {
            "iat": now,
            "exp": expires,
            "task_id": task_id,
            "validator_signatures": signatures,
            "consensus": consensus,
        }

        jwt_str = jwt.encode(payload, self.secret, algorithm="HS256")

        token = ValidationToken(
            jwt=jwt_str,
            task_id=task_id,
            validator_signatures=signatures,
            consensus=consensus,
            issued_at=now.isoformat(),
            expires_at=expires.isoformat(),
        )

        self._log_event("token_issued", {
            "task_ref": task_id,
            "token_id": self._token_id(jwt_str),
            "validators": signatures,
            "expires_at": expires.isoformat(),
        })

        return token

    def verify_token(
        self,
        token: Optional[ValidationToken],
        expected_task_id: Optional[str] = None,
    ) -> bool:
        """Verify token signature, expiry, and optional task binding.

        Args:
            token: ValidationToken to verify (or None)
            expected_task_id: If provided, also verify the token was
                              issued for this specific task

        Returns:
            True if token is valid, unexpired, task-bound (if specified)
        """
        if token is None or not token.jwt:
            return False

        if self._token_id(token.jwt) in self._used_tokens:
            self._log_event("token_consumed", {
                "task_ref": token.task_id or expected_task_id,
            })
            return False

        try:
            payload = jwt.decode(
                token.jwt,
                self.secret,
                algorithms=["HS256"],
                options={"require": ["exp", "iat", "task_id"]},
            )
        except jwt.ExpiredSignatureError:
            self._log_event("token_expired", {
                "task_ref": token.task_id or expected_task_id,
                "expires_at": token.expires_at,
            })
            return False
        except jwt.InvalidTokenError:
            self._log_event("token_invalid", {
                "task_ref": token.task_id or expected_task_id,
                "reason": "signature or format invalid",
            })
            return False

        if expected_task_id is not None and payload.get("task_id") != expected_task_id:
            self._log_event("token_task_mismatch", {
                "task_ref": expected_task_id,
                "token_task_ref": payload.get("task_id"),
            })
            return False

        return True

    def consume_token(self, token: Optional[ValidationToken]) -> None:
        """Mark a token as consumed (single-use enforcement).

        Args:
            token: ValidationToken to mark as used
        """
        if token is not None and token.jwt:
            self._used_tokens.add(self._token_id(token.jwt))

    def decode_token(self, token: Optional[ValidationToken]) -> Optional[Dict[str, Any]]:
        """Decode token payload for inspection (no signature verification).

        Args:
            token: ValidationToken to decode

        Returns:
            Payload dict, or None if token is None
        """
        if token is None or not token.jwt:
            return None
        try:
            return jwt.decode(token.jwt, options={"verify_signature": False})
        except jwt.InvalidTokenError:
            return None

    @staticmethod
    def _token_id(jwt_str: str) -> str:
        """Short stable identifier for a JWT (truncated SHA-256)."""
        return hashlib.sha256(jwt_str.encode()).hexdigest()[:16]

    def _log_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Log to injected audit log if available."""
        if self._audit_log is not None:
            self._audit_log.append_event(event_type, data)

    @staticmethod
    def _has_blockers(results: List[ValidatorResult]) -> bool:
        return any(r.severity == "blocker" for r in results)


# Default issuer (no audit_log, purely for convenience functions needing
# the old API surface.  Engine and MCP server inject their own instances.)
_default_issuer = TokenIssuer()


def issue_token(
    task_id: str,
    validator_results: List[ValidatorResult],
    consensus: str = "unanimous",
) -> Optional[ValidationToken]:
    """Convenience: issue a JWT-backed validation token (default issuer)."""
    return _default_issuer.issue_token(task_id, validator_results, consensus)


def verify_token(
    token: Optional[ValidationToken],
    expected_task_id: Optional[str] = None,
) -> bool:
    """Convenience: verify a JWT token (default issuer)."""
    return _default_issuer.verify_token(token, expected_task_id=expected_task_id)


def decode_token(token: Optional[ValidationToken]) -> Optional[Dict[str, Any]]:
    """Convenience: decode a JWT token payload (default issuer)."""
    return _default_issuer.decode_token(token)
