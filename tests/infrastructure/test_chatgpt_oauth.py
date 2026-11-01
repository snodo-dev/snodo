import json
import time

import pytest

from snodo.infrastructure import chatgpt_oauth as oauth


def setup_token(tmp_path, monkeypatch, expiry):
    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path))
    monkeypatch.setenv("CHATGPT_AUTH_FILE", "auth.json")
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"access_token": "old", "refresh_token": "refresh", "expires_at": expiry}))
    return path


def test_fresh_token_and_read_only_state(tmp_path, monkeypatch):
    setup_token(tmp_path, monkeypatch, time.time() + 3600)
    assert oauth.ensure_chatgpt_access_token("openai", authenticator=object()) == "old"
    assert oauth.chatgpt_sign_in_state()["signed_in"] is True


def test_expired_refresh_persists_private_permissions(tmp_path, monkeypatch):
    path = setup_token(tmp_path, monkeypatch, 0)

    class Auth:
        def refresh_access_token(self, data):
            return {"access_token": "new", "expires_at": time.time() + 3600}

    assert oauth.ensure_chatgpt_access_token("openai", authenticator=Auth()) == "new"
    assert path.stat().st_mode & 0o777 == 0o600
    assert tmp_path.stat().st_mode & 0o777 == 0o700


def test_missing_and_rejected_refresh_require_sign_in(tmp_path, monkeypatch):
    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path))
    with pytest.raises(oauth.ChatGPTReauthenticationRequired, match="--provider openai --authenticate"):
        oauth.ensure_chatgpt_access_token("openai", authenticator=object())
    setup_token(tmp_path, monkeypatch, 0)

    class Rejected:
        def refresh_access_token(self, data):
            raise RuntimeError("rejected")

    with pytest.raises(oauth.ChatGPTReauthenticationRequired, match="--provider openai --authenticate"):
        oauth.ensure_chatgpt_access_token("openai", authenticator=Rejected())


def test_unsigned_state(tmp_path, monkeypatch):
    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path))
    assert oauth.chatgpt_sign_in_state() is None
