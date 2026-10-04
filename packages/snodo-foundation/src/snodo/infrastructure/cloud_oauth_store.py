"""Private, durable storage for the user's cloud OAuth credentials.

The record lives under ``resolve_home()`` (never in a project directory).
Credential values are deliberately omitted from the dataclass representation.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from snodo.infrastructure.atomic_json import atomic_write_json
from snodo.paths import resolve_home

_STATE_FILE = "cloud_oauth.json"
_LOCK_FILE = ".cloud_oauth.lock"
_DEFAULT_MARGIN_SECONDS = 60.0


@dataclass(frozen=True, repr=False)
class CloudOAuthState:
    """OAuth client and token state; token fields never appear in repr."""

    client_id: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    expires_at: float | None = None
    scope: str | None = None
    registered_client_name: str | None = None
    supports_code_redirect: bool = False

    def __repr__(self) -> str:
        return (
            "CloudOAuthState(client_id_present="
            f"{self.client_id is not None}, access_token_present={self.access_token is not None}, "
            f"refresh_token_present={self.refresh_token is not None}, expires_at={self.expires_at!r}, "
            f"scope_present={self.scope is not None}, registered_client_name={self.registered_client_name!r})"
        )

    def is_expired(self, *, now: Callable[[], float] = time.time) -> bool:
        """Whether there is no usable access token or its absolute expiry passed."""
        return self.access_token is None or self.expires_at is None or now() >= self.expires_at

    def is_near_expiry(
        self,
        window_seconds: float = _DEFAULT_MARGIN_SECONDS,
        *,
        now: Callable[[], float] = time.time,
    ) -> bool:
        """Whether expiry has passed or is within *window_seconds*."""
        return self.access_token is None or self.expires_at is None or now() >= self.expires_at - window_seconds

    def has_refresh_token(self) -> bool:
        """Whether this state contains a refresh token."""
        return bool(self.refresh_token)


def state_path() -> Path:
    """Return the OAuth state file under the configured user home."""
    return resolve_home() / _STATE_FILE


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.parent / _LOCK_FILE
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def load_oauth_state() -> CloudOAuthState:
    """Load stored state; missing, malformed, or invalid data means logged out."""
    path = state_path()
    with _locked(path):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return CloudOAuthState()
            client_id = raw.get("client_id")
            access_token = raw.get("access_token")
            refresh_token = raw.get("refresh_token")
            expires_at = raw.get("expires_at")
            scope = raw.get("scope")
            registered_client_name = raw.get("registered_client_name")
            if any(value is not None and not isinstance(value, str) for value in (client_id, access_token, refresh_token, scope, registered_client_name)):
                return CloudOAuthState()
            if expires_at is not None and (isinstance(expires_at, bool) or not isinstance(expires_at, (int, float))):
                return CloudOAuthState()
            supports_code_redirect = raw.get("supports_code_redirect", False)
            if not isinstance(supports_code_redirect, bool):
                return CloudOAuthState()
            return CloudOAuthState(client_id, access_token, refresh_token, expires_at, scope, registered_client_name, supports_code_redirect)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return CloudOAuthState()


def save_oauth_state(state: CloudOAuthState) -> None:
    """Atomically replace the complete record (including both rotated tokens)."""
    if not isinstance(state, CloudOAuthState):
        raise TypeError("state must be a CloudOAuthState")
    path = state_path()
    payload = {
        "client_id": state.client_id,
        "access_token": state.access_token,
        "refresh_token": state.refresh_token,
        "expires_at": state.expires_at,
        "scope": state.scope,
        "registered_client_name": state.registered_client_name,
        "supports_code_redirect": state.supports_code_redirect,
    }
    with _locked(path):
        atomic_write_json(path, payload, trailing_newline=True)
        os.chmod(path, 0o600)


def clear_oauth_state(*, keep_client_id: bool = True) -> None:
    """Log out by clearing tokens, optionally retaining the registered client ID."""
    path = state_path()
    with _locked(path):
        client_id = None
        if keep_client_id:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("client_id"), str):
                    client_id = raw["client_id"]
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                pass
        if client_id is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        else:
            registered_client_name = None
            supports_code_redirect = False
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("registered_client_name"), str):
                    registered_client_name = raw["registered_client_name"]
                if isinstance(raw, dict):
                    supports_code_redirect = raw.get("supports_code_redirect", False) is True
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                pass
            atomic_write_json(path, {"client_id": client_id, "access_token": None, "refresh_token": None, "expires_at": None, "scope": None, "registered_client_name": registered_client_name, "supports_code_redirect": supports_code_redirect}, trailing_newline=True)
            os.chmod(path, 0o600)
