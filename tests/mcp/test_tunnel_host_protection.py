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
