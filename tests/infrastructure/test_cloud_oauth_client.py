from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from snodo.infrastructure.cloud_oauth_client import (
    CloudOAuthClient,
    CloudOAuthError,
    pkce_challenge,
)


def _transport(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _metadata():
    return {
        "issuer": "https://auth.example.test",
        "authorization_endpoint": "https://auth.example.test/authorize",
        "token_endpoint": "https://auth.example.test/token",
        "registration_endpoint": "https://auth.example.test/register",
        "jwks_uri": "https://auth.example.test/jwks",
    }


def test_discovery_validates_required_https_endpoints():
    for payload, message in [
        ({**_metadata(), "jwks_uri": None}, "missing required endpoint"),
        ({**_metadata(), "token_endpoint": "http://bad.test/token"}, "must use HTTPS"),
    ]:
        client = CloudOAuthClient(http_client=_transport(lambda request, p=payload: httpx.Response(200, json=p)))
        with pytest.raises(CloudOAuthError, match=message):
            client.discover()
    client = CloudOAuthClient(
        {"cloud": {"oauth_metadata_url": "http://localhost/metadata"}},
        http_client=_transport(lambda request: httpx.Response(200, json=_metadata())),
    )
    with pytest.raises(CloudOAuthError, match="must use HTTPS"):
        client.discover()


def test_register_pkce_authorize_exchange_and_refresh():
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_metadata())
        if request.url.path.endswith("register"):
            return httpx.Response(201, json={"client_id": "client-1"})
        return httpx.Response(200, json={"access_token": "secret-access", "refresh_token": "secret-refresh"})

    client = CloudOAuthClient(http_client=_transport(handler), hostname="host-a")
    client.metadata = None
    assert pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    client_id = client.register_client()
    assert client_id == "client-1"
    assert '"client_name":"snodo CLI (host-a)"' in requests[-1].read().decode()
    body = requests[-1].read().decode()
    assert 'http://localhost:*' in body
    assert requests[-1].headers["User-Agent"] == "snodo/0.19.3"
    assert requests[-1].headers["X-Snodo-Device"] == "host-a"
    verifier = "a" * 43
    url = client.authorization_url(client_id, "http://localhost:1234/callback", verifier, "state-1")
    query = parse_qs(urlsplit(url).query)
    assert query == {
        "response_type": ["code"], "client_id": ["client-1"],
        "redirect_uri": ["http://localhost:1234/callback"], "scope": ["cli"],
        "state": ["state-1"], "code_challenge": [pkce_challenge(verifier)],
        "code_challenge_method": ["S256"],
    }
    assert client.exchange_code(client_id, "authorization-secret", "http://localhost:1234/callback", verifier)["access_token"] == "secret-access"
    assert client.refresh(client_id, "refresh-secret")["refresh_token"] == "secret-refresh"
    assert "authorization-secret" in requests[-2].read().decode()


@pytest.mark.parametrize("status", [400, 500])
def test_oauth_errors_do_not_disclose_tokens(status):
    secret = "do-not-leak-this-token"

    def handler(request):
        return httpx.Response(status, text=f"invalid token: {secret}")

    client = CloudOAuthClient(http_client=_transport(handler))
    client.metadata = type("Metadata", (), {"token_endpoint": "https://auth.test/token"})()
    with pytest.raises(CloudOAuthError) as exc:
        client.refresh("client", secret)
    assert secret not in str(exc.value)


def test_network_failure_is_safe():
    client = CloudOAuthClient(http_client=_transport(lambda request: (_ for _ in ()).throw(OSError("secret-value"))))
    with pytest.raises(CloudOAuthError) as exc:
        client.discover()
    assert "secret-value" not in str(exc.value)
