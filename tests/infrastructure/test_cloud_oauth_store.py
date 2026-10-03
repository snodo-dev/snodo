import json

from snodo.infrastructure import cloud_oauth_store as store
from snodo.infrastructure.cloud_oauth_store import CloudOAuthState


def test_round_trip_is_private_and_replaces_atomically(tmp_path, monkeypatch):
    monkeypatch.setenv("SNODO_HOME", str(tmp_path / "user-home"))
    state = CloudOAuthState("client", "access", "refresh", 1234.0, "read write")
    store.save_oauth_state(state)
    path = tmp_path / "user-home" / "cloud_oauth.json"
    assert store.load_oauth_state() == state
    assert path.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / ".snodo" / "cloud_oauth.json").exists()

    replaced = CloudOAuthState("client", "new-access", "new-refresh", 5678.0, "read")
    store.save_oauth_state(replaced)
    assert store.load_oauth_state() == replaced
    assert json.loads(path.read_text())["refresh_token"] == "new-refresh"


def test_missing_or_corrupt_is_logged_out_and_never_leaks(tmp_path, monkeypatch):
    monkeypatch.setenv("SNODO_HOME", str(tmp_path / "home"))
    assert store.load_oauth_state() == CloudOAuthState()
    path = store.state_path()
    path.write_text("secret-refresh-token {", encoding="utf-8")
    state = store.load_oauth_state()
    assert state == CloudOAuthState()
    assert "secret-refresh-token" not in repr(state)


def test_clear_preserves_or_drops_client_id(tmp_path, monkeypatch):
    monkeypatch.setenv("SNODO_HOME", str(tmp_path / "home"))
    store.save_oauth_state(CloudOAuthState("client", "access", "refresh", 100, "scope"))
    store.clear_oauth_state()
    assert store.load_oauth_state() == CloudOAuthState(client_id="client")
    store.save_oauth_state(CloudOAuthState("client", "access", "refresh", 100, "scope"))
    store.clear_oauth_state(keep_client_id=False)
    assert store.load_oauth_state() == CloudOAuthState()
    assert not store.state_path().exists()


def test_expiry_helpers_use_injected_clock():
    state = CloudOAuthState(access_token="access", expires_at=150, refresh_token="refresh")
    assert not state.is_expired(now=lambda: 149)
    assert state.is_expired(now=lambda: 150)
    assert not state.is_near_expiry(20, now=lambda: 129)
    assert state.is_near_expiry(20, now=lambda: 130)
    assert state.has_refresh_token()
    assert not CloudOAuthState().has_refresh_token()
