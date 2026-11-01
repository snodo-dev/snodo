"""Process-safe refresh of LiteLLM's ChatGPT subscription credentials."""
from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Iterator

_THREAD_LOCK = RLock()
SAFETY_MARGIN_SECONDS = 300.0


class ChatGPTReauthenticationRequired(RuntimeError):
    """Stored ChatGPT credentials are absent or cannot be refreshed."""


def token_path() -> Path:
    """Resolve LiteLLM's configured ChatGPT token path."""
    directory = os.environ.get("CHATGPT_TOKEN_DIR")
    if directory is None:
        directory = str(Path.home() / ".config" / "litellm" / "chatgpt")
    return Path(directory) / os.environ.get("CHATGPT_AUTH_FILE", "auth.json")


@contextmanager
def chatgpt_token_lock() -> Iterator[None]:
    """Lock ChatGPT token operations across threads and processes."""
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd = os.open(path.with_name(path.name + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        with _THREAD_LOCK:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _read(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("invalid token record")
    return raw


def chatgpt_sign_in_state() -> dict | None:
    """Return signed-in state (including expiry), without making network calls."""
    try:
        data = _read(token_path())
        if not data.get("access_token"):
            return None
        return {"signed_in": True, "expires_at": data.get("expires_at")}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def ensure_chatgpt_access_token(provider_name: str, *, authenticator=None,
                                safety_margin: float = SAFETY_MARGIN_SECONDS) -> str:
    """Return a sufficiently fresh token, refreshing at most once under lock."""
    path = token_path()
    try:
        with chatgpt_token_lock():
            data = _read(path)
            if data.get("access_token") and float(data.get("expires_at", 0)) > time.time() + safety_margin:
                return data["access_token"]
            if authenticator is None:
                from litellm.llms.chatgpt.authenticator import ChatGPTAuthenticator
                authenticator = ChatGPTAuthenticator()
            try:
                refresh = getattr(authenticator, "refresh_access_token")
                updated = refresh(data)
                if not isinstance(updated, dict) or not updated.get("access_token"):
                    raise ValueError("invalid refresh response")
                data.update(updated)
                path.write_text(json.dumps(data), encoding="utf-8")
                os.chmod(path, 0o600)
                os.chmod(path.parent, 0o700)
                return data["access_token"]
            except Exception as exc:
                raise ChatGPTReauthenticationRequired(
                    f"ChatGPT sign-in is required; run `snodo models --provider {provider_name} --authenticate` again."
                ) from exc
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ChatGPTReauthenticationRequired(
            f"ChatGPT sign-in is required; run `snodo models --provider {provider_name} --authenticate` again."
        ) from exc
