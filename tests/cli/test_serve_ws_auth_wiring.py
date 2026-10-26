"""Composed ws tunnel auth wiring for private and public hosts."""

import anyio
from mcp.server.mcpserver import MCPServer
from starlette.testclient import TestClient

from snodo.cli.commands.serve_security import build_transport_security_settings
from snodo.cli.commands.serve_tunnel_asgi import install_tunnel_mcp_paths
from snodo.cli.commands.serve_ws_auth import configure_tunnel_auth

PRIVATE_HOST = "demo-producer-abc123-tunnel.snodo.dev"
PUBLIC_HOST = "demo-pub.snodo.dev"
PORT = 8765
JSON_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


def test_ws_auth_wiring_composes_hosts_audiences_and_organization(monkeypatch, capsys):
    from snodo.infrastructure.jwks import JwksClient

    def jwks_verify(token, audience):
        return (
            {"sub": "user", "org_id": "org-2" if token == "other-org" else "org-1"}
            if token == audience or token == "other-org" else None
        )
    monkeypatch.setattr(JwksClient, "fetch", lambda self: True)
    monkeypatch.setattr(JwksClient, "verify", lambda self, token, audience: jwks_verify(token, audience))
    config = {
        "transport": "ws", "hostname": PRIVATE_HOST,
        "public_hostname": PUBLIC_HOST, "org_id": "org-1",
    }
    result = configure_tunnel_auth(".", lambda _root: config)
    _, private, ws, public, hosts, _, oauth, _, kwargs = result
    assert (private, ws, public, set(hosts), oauth) == (
        PRIVATE_HOST, True, PUBLIC_HOST, {PRIVATE_HOST, PUBLIC_HOST}, True
    )
    verifier = kwargs["token_verifier"]
    settings = kwargs["auth_settings"]
    assert settings.validate_token_resource is False

    mcp = MCPServer("ws-auth-wiring", token_verifier=verifier, auth=settings)

    @mcp.tool()
    def ping() -> str:
        return "pong"

    install_tunnel_mcp_paths(mcp, verifier, oauth_enabled=True, hosts=set(hosts), private_host=private)
    app = mcp.streamable_http_app(
        stateless_http=True,
        transport_security=build_transport_security_settings(PORT, private, public),
    )

    async def run():
        async with anyio.create_task_group() as tg:
            mcp.session_manager._task_group = tg
            client = TestClient(app)

            def post(host, token):
                return client.post("/mcp", content=b'{"jsonrpc":"2.0","method":"ping","id":1}',
                                   headers={**JSON_HEADERS, "host": host, "authorization": f"Bearer {token}"})

            private_token = f"https://{PRIVATE_HOST}/mcp"
            public_token = f"https://{PUBLIC_HOST}/mcp"
            assert post(PRIVATE_HOST, private_token).status_code == 200
            assert post(PUBLIC_HOST, public_token).status_code == 200
            assert post(PRIVATE_HOST, public_token).status_code == 401
            assert post(PUBLIC_HOST, private_token).status_code == 401
            assert post(PRIVATE_HOST, "other-org").status_code == 403
            assert post("evil.example.com", private_token).status_code == 421
            assert post(f"127.0.0.1:{PORT}", private_token).status_code == 200
            for host in (PRIVATE_HOST, PUBLIC_HOST):
                response = client.get("/.well-known/oauth-protected-resource", headers={"host": host})
                assert response.status_code == 200
                assert response.json()["resource"] == f"https://{host}/mcp"
            response = client.get("/.well-known/oauth-protected-resource", headers={"host": f"127.0.0.1:{PORT}"})
            assert response.json()["resource"] == f"https://{PRIVATE_HOST}/mcp"
            tg.cancel_scope.cancel()

    anyio.run(run)


def test_ws_public_alias_without_org_is_not_installed(monkeypatch):
    from snodo.infrastructure.jwks import JwksClient

    monkeypatch.setattr(JwksClient, "fetch", lambda self: True)
    monkeypatch.setattr(JwksClient, "verify", lambda self, token, audience: {"org_id": "org-1"})
    config = {"transport": "ws", "hostname": PRIVATE_HOST, "public_hostname": PUBLIC_HOST}
    result = configure_tunnel_auth(".", lambda _root: config)
    hosts = set(result[4])
    assert hosts == {PRIVATE_HOST}

    verifier = result[8]["token_verifier"]
    mcp = MCPServer("ws-missing-org", token_verifier=verifier, auth=result[8]["auth_settings"])
    install_tunnel_mcp_paths(mcp, verifier, oauth_enabled=True, hosts=hosts, private_host=PRIVATE_HOST)
    app = mcp.streamable_http_app(
        stateless_http=True,
        transport_security=build_transport_security_settings(PORT, PRIVATE_HOST, PUBLIC_HOST),
    )
    response = TestClient(app).get("/.well-known/oauth-protected-resource", headers={"host": PUBLIC_HOST})
    assert response.status_code == 421
