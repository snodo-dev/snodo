"""JWT-based token integrity subsystem (INV1).

FILE: snodo/infrastructure/tokens.py (Task 7.7)

Consolidated token model using PyJWT for standard signing, expiry,
and tamper detection.  Single-use enforcement is backed by a shared
SQLite store (``~/.snodo/tokens.db``) so consumption is atomic across
processes and survives restarts.

Standard claims: iat (issued at), exp (expiry at)
Custom claims:  task_id, validator_signatures, consensus
"""

import hashlib
import logging
import os
import secrets
import sqlite3
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import jwt

from snodo.core.interfaces import ValidatorResult
from snodo.paths import resolve_token_store

_logger = logging.getLogger(__name__)


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


class TokenStoreError(TokenError):
    """The consumed-token store could not be opened/read/written (fail closed)."""


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


class TokenStore:
    """SQLite-backed consumed-token store (single-use enforcement).

    The INSERT is the claim: success = first holder; ``IntegrityError`` =
    already consumed.  This gives atomic compare-and-set across processes —
    there is no read-then-write and no application-level locking.

    The store is created lazily on first use (``mkdir(parents=True)``), NOT at
    ``snodo init``, so existing installs keep working without re-init.
    """

    _PRUNE_EVERY = 100

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else resolve_token_store()
        self._conn: Optional[sqlite3.Connection] = None
        self._inserts_since_prune = 0

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA user_version=1")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS consumed_tokens ("
                " token_id TEXT PRIMARY KEY,"
                " task_id TEXT NOT NULL,"
                " exp INTEGER NOT NULL"
                ")"
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001 — fail closed
            raise TokenStoreError(
                f"Could not open token store at {self._path}: {e}"
            ) from e
        self._conn = conn
        self._prune()
        return conn

    def is_consumed(self, token_id: str) -> bool:
        """Return True if *token_id* has already been consumed."""
        try:
            conn = self._connect()
            cur = conn.execute(
                "SELECT 1 FROM consumed_tokens WHERE token_id = ?", (token_id,)
            )
            return cur.fetchone() is not None
        except TokenStoreError:
            raise
        except Exception as e:  # noqa: BLE001 — fail closed
            raise TokenStoreError(f"Could not read token store: {e}") from e

    def consume(self, token_id: str, task_id: str, exp: int) -> bool:
        """Atomically claim a token.

        Returns True if this call consumed it (first holder), False if it was
        already consumed.  Raises TokenStoreError if the store is unwritable.
        """
        try:
            conn = self._connect()
            conn.execute(
                "INSERT INTO consumed_tokens (token_id, task_id, exp) "
                "VALUES (?, ?, ?)",
                (token_id, task_id, exp),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return False
        except TokenStoreError:
            raise
        except Exception as e:  # noqa: BLE001 — fail closed
            raise TokenStoreError(f"Could not write token store: {e}") from e

        self._inserts_since_prune += 1
        if self._inserts_since_prune >= self._PRUNE_EVERY:
            self._prune()
        return True

    def _prune(self) -> None:
        """Opportunistically delete expired tokens (no scheduler)."""
        try:
            conn = self._connect()
            conn.execute(
                "DELETE FROM consumed_tokens WHERE exp < ?", (int(time.time()),)
            )
            conn.commit()
            self._inserts_since_prune = 0
        except Exception:  # noqa: BLE001 — opportunistic
            pass


class TokenIssuer:
    """Issues and verifies JWT validation tokens.

    Tokens are HS256-signed JWTs with standard iat/exp claims
    and custom claims for task_id, validator_signatures, and consensus.
    PyJWT handles signature verification and expiry automatically.

    Single-use: ``verify_token`` CHECKS the shared consumed-token store but does
    NOT consume (a dispatch may involve many mutating tool calls).  Consumption
    happens at the dispatch boundary via ``consume_token``.
    """

    def __init__(
        self,
        secret: Optional[str] = None,
        ttl_seconds: int = 600,
        audit_log: Any = None,
        store_path: Optional[Path] = None,
    ):
        self.secret = self._resolve_secret(secret)
        self.ttl_seconds = ttl_seconds
        self._audit_log = audit_log
        self._store = TokenStore(store_path)

    @staticmethod
    def _resolve_secret(secret: Optional[str]) -> str:
        if secret:
            return secret
        env = os.environ.get("SNODO_TOKEN_SECRET")
        if env is not None:
            if env == "":
                raise TokenError(
                    "SNODO_TOKEN_SECRET is set but empty — refusing to use an "
                    "empty signing secret."
                )
            return env
        # Fall back to a random per-process secret, but warn loudly: tokens
        # will NOT verify across processes (engine <-> MCP) without a shared
        # secret.
        warnings.warn(
            "SNODO_TOKEN_SECRET is not set — using a random per-process secret. "
            "Tokens will not verify across processes (engine <-> MCP). Set "
            "SNODO_TOKEN_SECRET to a shared secret for cross-process validation.",
            stacklevel=2,
        )
        return secrets.token_hex(32)

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
        """Verify token signature, expiry, task binding, and single-use.

        Checks the shared consumed-token store but does NOT consume (a dispatch
        may involve many mutating tool calls).  Fails closed: if the store
        cannot be read, raises TokenStoreError rather than accepting the token.

        Args:
            token: ValidationToken to verify (or None)
            expected_task_id: If provided, also verify the token was
                              issued for this specific task

        Returns:
            True if token is valid, unexpired, task-bound (if specified),
            and not already consumed.
        """
        if token is None or not token.jwt:
            return False

        token_id = self._token_id(token.jwt)
        if self._store.is_consumed(token_id):
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

    def consume_token(self, token: Optional[ValidationToken]) -> bool:
        """Atomically mark a token as consumed (single-use enforcement).

        Called at the dispatch boundary.  Returns True if this call consumed
        the token (first holder), False if it was already consumed.  Raises
        TokenStoreError if the store is unwritable (fail closed).
        """
        if token is None or not token.jwt:
            return False
        token_id = self._token_id(token.jwt)
        exp = self._decode_exp(token.jwt)
        return self._store.consume(token_id, token.task_id or "", exp)

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

    @staticmethod
    def _decode_exp(jwt_str: str) -> int:
        """Decode the exp claim (epoch seconds) without signature verification."""
        try:
            payload = jwt.decode(jwt_str, options={"verify_signature": False})
            return int(payload.get("exp", 0))
        except Exception:  # noqa: BLE001 — invalid token, exp irrelevant
            return 0

    def _log_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Log to injected audit log if available."""
        if self._audit_log is not None:
            self._audit_log.append_event(event_type, data)

    @staticmethod
    def _has_blockers(results: List[ValidatorResult]) -> bool:
        return any(r.severity == "blocker" for r in results)
