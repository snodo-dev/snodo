"""ASGI path/auth boundary tests for managed tunnels."""

import asyncio
import base64
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from snodo.infrastructure.access_assertion import AccessAssertionVerifier
from snodo.infrastructure.jwks import JwksClient

from snodo.cli.commands.serve_tunnel_asgi import TunnelMCPPaths


class FakeAccessVerifier:
    def verify(self, token):
        return token == "access-valid"


class FakeOAuthApp:
    def __init__(self):
        self.requests = []

    async def __call__(self, scope, receive, send):
        self.requests.append((scope["path"], dict(scope.get("headers", []))))
        status = 200 if scope["path"] == "/mcp" else 404
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"handled"})


def request(app, path, headers=()):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(app({"type": "http", "path": path, "raw_path": path.encode(),
                     "method": "POST", "headers": list(headers)}, receive, send))
    return messages[0]["status"]


def test_access_path_checks_assertion_before_same_mcp_handler():
    app = FakeOAuthApp()
    wrapped = TunnelMCPPaths(app, FakeAccessVerifier(), oauth_enabled=True)
    assert request(wrapped, "/mcp-st", [(b"cf-access-jwt-assertion", b"access-valid")]) == 200
    assert app.requests[0][0] == "/mcp"
    for token in (None, b"oauth-token", b"expired", b"bad-signature", b"wrong-audience"):
        headers = [] if token is None else [(b"cf-access-jwt-assertion", token)]
        assert request(wrapped, "/mcp-st", headers) == 401
    assert len(app.requests) == 1


def test_oauth_only_remains_unwrapped_and_both_paths_are_independent():
    app = FakeOAuthApp()
    # OAuth-only runs without the service-token wrapper at all.
    assert request(app, "/mcp", [(b"authorization", b"Bearer oauth")]) == 200
    assert request(app, "/mcp-st") == 404  # no route is installed for OAuth-only

    app = FakeOAuthApp()
    wrapped = TunnelMCPPaths(app, FakeAccessVerifier(), oauth_enabled=True)
    assert request(wrapped, "/mcp", [(b"authorization", b"Bearer oauth")]) == 200
    assert request(wrapped, "/mcp-st", [(b"authorization", b"Bearer oauth")]) == 401
    assert request(wrapped, "/mcp-st", [(b"cf-access-jwt-assertion", b"access-valid")]) == 200
    assert len(app.requests) == 2


def test_service_token_only_refuses_oauth_path():
    app = FakeOAuthApp()
    wrapped = TunnelMCPPaths(app, FakeAccessVerifier(), oauth_enabled=False)
    assert request(wrapped, "/mcp") == 401
    assert request(wrapped, "/mcp-st", [(b"cf-access-jwt-assertion", b"access-valid")]) == 200
    assert [path for path, _ in app.requests] == ["/mcp"]


def test_asgi_access_path_uses_real_verifier_with_fake_keyset():
    domain, audience, kid = "team.cloudflareaccess.com", "access-aud", "test-key"
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private_key.public_key().public_numbers()

    def b64(number):
        raw = number.to_bytes((number.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    jwk = {"kty": "RSA", "n": b64(numbers.n), "e": b64(numbers.e), "alg": "RS256", "kid": kid}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"keys": [jwk]}

    client = JwksClient(f"https://{domain}/cdn-cgi/access/certs",
                        fetcher=lambda *_a, **_k: Response())
    verifier = AccessAssertionVerifier(domain, audience, jwks_client=client)
    app = FakeOAuthApp()
    wrapped = TunnelMCPPaths(app, verifier, oauth_enabled=False)

    def token(*, aud=audience, exp=None, key=private_key):
        claims = {"iss": f"https://{domain}", "aud": aud,
                  "exp": exp if exp is not None else int(time.time()) + 60}
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})

    valid = token()
    assert request(wrapped, "/mcp-st", [(b"cf-access-jwt-assertion", valid.encode())]) == 200
    bad_signature = token(key=rsa.generate_private_key(public_exponent=65537, key_size=2048))
    invalid = [None, token(aud="wrong"), token(exp=int(time.time()) - 1), bad_signature]
    for assertion in invalid:
        headers = [] if assertion is None else [(b"cf-access-jwt-assertion", assertion.encode())]
        assert request(wrapped, "/mcp-st", headers) == 401
    assert len(app.requests) == 1
