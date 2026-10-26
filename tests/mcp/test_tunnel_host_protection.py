"""The tunnel path wrapper keeps mcp 2.x Host protection in front of both HTTP apps.

``install_tunnel_mcp_paths`` wraps ``streamable_http_app`` and ``sse_app`` so that
``TunnelMCPPaths`` sits in front of the real app. On mcp 2.x the factories take
``host`` and ``transport_security``; a wrapper that dropped either would serve an
app that refuses the tunnel hostname (the SDK default only knows loopback). These
tests drive the real wrapper, the real security settings and the real SDK apps in
process, so such a regression fails here instead of in a tunnel.
"""

import anyio
from mcp.server.mcpserver import MCPServer
from starlette.testclient import TestClient

from snodo.cli.commands.serve_security import build_transport_security_settings
from snodo.cli.commands.serve_tunnel_asgi import (
    TunnelMCPPaths,
    install_tunnel_mcp_paths,
)

TUNNEL_HOST = "demo-producer-abc123-tunnel.snodo.dev"
FOREIGN_HOST = "evil.example.com"
PORT = 8765
JSON_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


class _AccessVerifier:
    """Stand-in for the Cloudflare Access verifier; the Host check is the subject."""

    def verify(self, _assertion):
        return True


def _wrapped_server() -> MCPServer:
    mcp = MCPServer("tunnel-host-protection")
    install_tunnel_mcp_paths(mcp, _AccessVerifier(), oauth_enabled=True)
    return mcp


def _post(client: TestClient, path: str, host: str) -> int:
    response = client.post(path, content=b"{}", headers={**JSON_HEADERS, "host": host})
    return response.status_code


def test_streamable_http_wrapper_keeps_host_protection():
    security = build_transport_security_settings(PORT, TUNNEL_HOST)

    async def run():
        mcp = _wrapped_server()
        app = mcp.streamable_http_app(
            stateless_http=True,
            host="127.0.0.1",
            transport_security=security,
        )
        assert isinstance(app, TunnelMCPPaths)
        with anyio.fail_after(10):
            async with anyio.create_task_group() as tg:
                mcp.session_manager._task_group = tg
                client = TestClient(app)
                assert _post(client, "/mcp", TUNNEL_HOST) != 421
                assert _post(client, "/mcp", FOREIGN_HOST) == 421
                tg.cancel_scope.cancel()

    anyio.run(run)


def test_sse_wrapper_keeps_host_protection():
    security = build_transport_security_settings(PORT, TUNNEL_HOST)
    mcp = _wrapped_server()
    app = mcp.sse_app(host="127.0.0.1", transport_security=security)
    assert isinstance(app, TunnelMCPPaths)
    client = TestClient(app)
    # The message endpoint checks Host before anything else and answers at once;
    # the event stream itself never ends, so it is not used here.
    assert _post(client, "/messages/", TUNNEL_HOST) != 421
    assert _post(client, "/messages/", FOREIGN_HOST) == 421


def test_host_set_serves_host_specific_protected_resource():
    private_host = TUNNEL_HOST
    public_host = "demo-pub.snodo.dev"
    mcp = MCPServer("tunnel-host-specific-discovery")
    install_tunnel_mcp_paths(
        mcp, _AccessVerifier(), oauth_enabled=True, hosts={private_host, public_host}
    )
    app = mcp.streamable_http_app(
        stateless_http=True,
        transport_security=build_transport_security_settings(PORT, private_host, public_host),
    )
    client = TestClient(app)

    for host in (private_host, public_host):
        response = client.get(
            "/.well-known/oauth-protected-resource", headers={"host": host}
        )
        assert response.status_code == 200
        assert response.json()["resource"] == f"https://{host}/mcp"

    response = client.get(
        "/.well-known/oauth-protected-resource", headers={"host": FOREIGN_HOST}
    )
    assert response.status_code == 421


def test_host_set_serves_both_protected_resource_forms_with_issuer():
    from mcp.server.auth.settings import AuthSettings
    from pydantic import AnyHttpUrl

    private_host = TUNNEL_HOST
    public_host = "demo-pub.snodo.dev"
    issuer = "https://mcp-auth.snodo.dev"
    mcp = MCPServer(
        "tunnel-host-specific-discovery-forms",
        token_verifier=_AccessVerifier(),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(issuer),
            resource_server_url=AnyHttpUrl(f"https://{private_host}/mcp"),
        ),
    )
    install_tunnel_mcp_paths(
        mcp, _AccessVerifier(), oauth_enabled=True, hosts={private_host, public_host},
        private_host=private_host, issuer_url=AnyHttpUrl(issuer),
    )
    app = mcp.streamable_http_app(
        stateless_http=True,
        transport_security=build_transport_security_settings(PORT, private_host, public_host),
    )
    client = TestClient(app)

    for host in (private_host, public_host):
        for path in (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
        ):
            response = client.get(path, headers={"host": host})
            assert response.status_code == 200
            assert response.json()["resource"] == f"https://{host}/mcp"
            assert response.json()["authorization_servers"] == [f"{issuer}/"]
            assert response.headers["cache-control"] == "no-store"

    response = client.get(
        "/.well-known/oauth-protected-resource/mcp", headers={"host": FOREIGN_HOST}
    )
    assert response.status_code == 421

    response = client.get(
        "/.well-known/oauth-protected-resource/mcp",
        headers={"host": f"localhost:{PORT}"},
    )
    assert response.status_code == 200
    assert response.json()["resource"] == f"https://{private_host}/mcp"


def test_protected_resource_without_hosts_setting_is_unchanged():
    mcp = _wrapped_server()
    app = mcp.streamable_http_app(stateless_http=True)
    response = TestClient(app).get("/.well-known/oauth-protected-resource/mcp")
    assert response.status_code != 200


def test_host_set_scopes_real_oauth_verifier_to_request_host():
    from unittest.mock import MagicMock

    from mcp.server.auth.settings import AuthSettings
    from pydantic import AnyHttpUrl
    from snodo.infrastructure.jwks import JwksClient
    from snodo.infrastructure.oauth_verifier import JwksTokenVerifier

    private_host = TUNNEL_HOST
    public_host = "demo-pub.snodo.dev"
    jwks = MagicMock(spec=JwksClient)
    jwks.verify.side_effect = lambda token, audience: (
        {"sub": "test-user", "scopes": []} if token == audience else None
    )
    verifier = JwksTokenVerifier(
        jwks,
        audiences={
            private_host: f"https://{private_host}/mcp",
            public_host: f"https://{public_host}/mcp",
        },
    )
    mcp = MCPServer(
        "tunnel-host-specific-audience",
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl("https://auth.snodo.dev"),
            resource_server_url=AnyHttpUrl(f"https://{private_host}/mcp"),
            validate_token_resource=False,
        ),
    )
    install_tunnel_mcp_paths(
        mcp, verifier, oauth_enabled=True, hosts={private_host, public_host}
    )
    app = mcp.streamable_http_app(
        stateless_http=True,
        transport_security=build_transport_security_settings(PORT, private_host, public_host),
    )

    async def run():
        async with anyio.create_task_group() as tg:
            mcp.session_manager._task_group = tg
            client = TestClient(app)

            def post(host, token):
                return client.post(
                    "/mcp",
                    content=b'{"jsonrpc":"2.0","method":"ping","id":1}',
                    headers={
                        **JSON_HEADERS,
                        "host": host,
                        "authorization": f"Bearer {token}",
                    },
                )

            assert post(private_host, f"https://{public_host}/mcp").status_code == 401
            assert post(public_host, f"https://{public_host}/mcp").status_code == 200
            assert post(public_host, f"https://{private_host}/mcp").status_code == 401
            tg.cancel_scope.cancel()

    anyio.run(run)


def test_other_organization_status_translation_on_both_real_apps():
    from unittest.mock import MagicMock

    from mcp.server.auth.settings import AuthSettings
    from pydantic import AnyHttpUrl
    from snodo.infrastructure.oauth_verifier import JwksTokenVerifier

    jwks = MagicMock()
    jwks.verify.side_effect = lambda token, audience: (
        {"sub": "user", "org_id": "org-2" if token == "other-org" else "org-1"}
        if token in ("matching", "other-org") else None
    )
    verifier = JwksTokenVerifier(jwks, expected_org_id="org-1")
    mcp = MCPServer(
        "tunnel-organization-status", token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl("https://auth.snodo.dev"),
            resource_server_url=AnyHttpUrl(f"https://{TUNNEL_HOST}/mcp"),
            validate_token_resource=False,
        ),
    )
    install_tunnel_mcp_paths(mcp, verifier, oauth_enabled=True)
    security = build_transport_security_settings(PORT, TUNNEL_HOST)
    apps = [
        mcp.streamable_http_app(stateless_http=True, host="127.0.0.1", transport_security=security),
        mcp.sse_app(host="127.0.0.1", transport_security=security),
    ]

    async def run():
        async with anyio.create_task_group() as tg:
            mcp.session_manager._task_group = tg
            for index, app in enumerate(apps):
                client = TestClient(app)
                path = "/mcp" if index == 0 else "/messages/"
                request_headers = {"host": TUNNEL_HOST, "content-type": "application/json"}
                if index == 0:
                    request_headers.update(JSON_HEADERS)
                body = b'{"jsonrpc":"2.0","method":"ping","id":1}'
                assert client.post(path, content=body, headers=request_headers).status_code == 401
                for token, expected in (("invalid", 401), ("other-org", 403), ("matching", 200)):
                    response = client.post(
                        path, content=body,
                        headers={**request_headers, "authorization": f"Bearer {token}"},
                    )
                    if index == 1 and token == "matching":
                        assert response.status_code == 400, response.text
                    else:
                        assert response.status_code == expected, response.text
            tg.cancel_scope.cancel()

    anyio.run(run)


def test_host_set_rewrites_real_oauth_401_challenge_per_request_host():
    from mcp.server.auth.settings import AuthSettings
    from pydantic import AnyHttpUrl
    from snodo.infrastructure.oauth_verifier import JwksTokenVerifier
    from unittest.mock import MagicMock

    private_host = TUNNEL_HOST
    public_host = "demo-pub.snodo.dev"
    jwks = MagicMock()
    jwks.verify.return_value = None
    verifier = JwksTokenVerifier(jwks)
    mcp = MCPServer(
        "tunnel-host-specific-challenge",
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl("https://mcp-auth.snodo.dev"),
            resource_server_url=AnyHttpUrl(f"https://{private_host}/mcp"),
            validate_token_resource=False,
        ),
    )
    install_tunnel_mcp_paths(
        mcp, verifier, oauth_enabled=True, hosts={private_host, public_host},
        private_host=private_host,
    )
    app = mcp.streamable_http_app(
        stateless_http=True,
        transport_security=build_transport_security_settings(PORT, private_host, public_host),
    )

    async def run():
        async with anyio.create_task_group() as tg:
            mcp.session_manager._task_group = tg
            client = TestClient(app)
            for host in (private_host, public_host):
                response = client.post(
                    "/mcp", content=b'{"jsonrpc":"2.0","method":"ping","id":1}',
                    headers={**JSON_HEADERS, "host": host},
                )
                assert response.status_code == 401
                assert f'resource_metadata="https://{host}/.well-known/oauth-protected-resource/mcp"' in response.headers["www-authenticate"]
            tg.cancel_scope.cancel()

    anyio.run(run)
