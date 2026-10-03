import httpx
import pytest

from snodo.infrastructure.cloud_credentials import safe_cloud_sync_credential
from snodo.infrastructure.cloud_oauth_store import CloudOAuthState, save_oauth_state
from snodo.infrastructure.cloud_lease import CloudSyncState, _perform_exchange, reset_admission_state


@pytest.fixture
def oauth_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SNODO_HOME", str(tmp_path))
    return tmp_path


def _transport(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _config():
    return {"cloud": {
        "api_key": "configured-api-key",
        "oauth_metadata_url": "https://auth.example.test/.well-known/oauth-authorization-server",
    }}


def test_expired_oauth_without_refresh_falls_back_to_api_key(oauth_home, caplog):
    save_oauth_state(CloudOAuthState("client", "expired-access", None, 0))
    credential, is_oauth = safe_cloud_sync_credential(_config())
    assert (credential, is_oauth) == ("configured-api-key", False)
    assert "snodo cloud login" in caplog.text
    assert "configured-api-key" not in caplog.text


def test_refused_refresh_falls_back_to_api_key(oauth_home, caplog):
    save_oauth_state(CloudOAuthState("client", "expired-access", "refresh-secret", 0))
    client = _transport(lambda request: httpx.Response(400, text="invalid_grant"))
    credential, is_oauth = safe_cloud_sync_credential(_config(), http_client=client)
    assert (credential, is_oauth) == ("configured-api-key", False)
    assert "refresh-secret" not in caplog.text


def test_working_oauth_precedes_api_key(oauth_home):
    save_oauth_state(CloudOAuthState("client", "usable-access", "refresh-secret", 4_000_000_000))
    assert safe_cloud_sync_credential(_config()) == ("usable-access", True)


def test_401_and_failed_refresh_retries_lease_with_api_key(oauth_home, monkeypatch, caplog):
    from snodo.infrastructure import cloud_lease, cloud_oauth_client

    save_oauth_state(CloudOAuthState("client", "expired-access", "refresh-secret", 0))
    reset_admission_state()
    authorizations = []

    def post(url, *, headers, timeout):
        authorizations.append(headers["Authorization"])
        if len(authorizations) == 1:
            return httpx.Response(401, request=httpx.Request("POST", url))
        return httpx.Response(200, json={"jti": "lease-id", "token": "opaque-lease"}, request=httpx.Request("POST", url))

    class FailedRefresh:
        def __init__(self, *args, **kwargs):
            pass

        def refresh(self, *args):
            raise RuntimeError("refresh refused")

    monkeypatch.setattr(cloud_lease.httpx, "post", post)
    monkeypatch.setattr(cloud_oauth_client, "CloudOAuthClient", FailedRefresh)
    lease = _perform_exchange(
        "expired-access", "https://cloud.example.test", "session",
        CloudSyncState(oauth_home / "cloud_sync.json"), oauth=True, config=_config(),
    )
    assert lease is not None
    assert authorizations == ["Bearer expired-access", "Bearer configured-api-key"]
    assert "refresh-secret" not in caplog.text


def test_failed_oauth_without_api_key_preserves_login_guidance(oauth_home, caplog):
    save_oauth_state(CloudOAuthState("client", "expired-access", None, 0))
    config = {"cloud": {"api_key": ""}}
    assert safe_cloud_sync_credential(config) == (None, True)
    assert "snodo cloud login" in caplog.text
