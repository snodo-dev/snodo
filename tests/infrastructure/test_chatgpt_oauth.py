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


def test_fresh_token_does_not_wait_for_refresh_lock(tmp_path, monkeypatch):
    setup_token(tmp_path, monkeypatch, time.time() + 3600)
    import threading

    entered = threading.Event()
    release = threading.Event()

    def hold_lock():
        with oauth.chatgpt_token_lock():
            entered.set()
            release.wait(5)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert entered.wait(2)
    try:
        assert oauth.ensure_chatgpt_access_token("openai", authenticator=object()) == "old"
    finally:
        release.set()
        holder.join(2)


def test_threaded_expired_token_refreshes_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock

    setup_token(tmp_path, monkeypatch, 0)
    calls = 0
    calls_lock = Lock()

    class Auth:
        def refresh_access_token(self, data):
            nonlocal calls
            with calls_lock:
                calls += 1
            time.sleep(0.05)
            return {"access_token": "new", "expires_at": time.time() + 3600}

    auth = Auth()
    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(lambda _: oauth.ensure_chatgpt_access_token("openai", authenticator=auth), range(16)))
    assert tokens == ["new"] * 16
    assert calls == 1


def _process_refresh(directory, counter):
    import os
    import time as process_time

    os.environ["CHATGPT_TOKEN_DIR"] = directory
    os.environ["CHATGPT_AUTH_FILE"] = "auth.json"

    class Auth:
        def refresh_access_token(self, data):
            counter.value += 1
            process_time.sleep(0.1)
            return {"access_token": "process-new", "expires_at": process_time.time() + 3600}

    return oauth.ensure_chatgpt_access_token("openai", authenticator=Auth())


def test_processes_refresh_expired_token_once(tmp_path, monkeypatch):
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    setup_token(tmp_path, monkeypatch, 0)
    context = multiprocessing.get_context("fork")
    manager = context.Manager()
    counter = manager.Value("i", 0)
    with ProcessPoolExecutor(max_workers=4, mp_context=context) as pool:
        tokens = list(pool.map(_process_refresh, [str(tmp_path)] * 8, [counter] * 8))
    assert tokens == ["process-new"] * 8
    assert counter.value == 1
    manager.shutdown()
