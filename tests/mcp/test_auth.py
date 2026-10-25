"""Tests for FastMCP OAuth 2.1 authentication integration.

FILE: tests/mcp/test_auth.py
"""

import json
from typing import Optional
from unittest.mock import MagicMock

import anyio
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from snodo.infrastructure.jwks import JwksClient
from snodo.infrastructure.oauth_verifier import JwksTokenVerifier
from starlette.testclient import TestClient


def _make_mcp(
    verifier: Optional[TokenVerifier] = None,
) -> MCPServer:
    """Create a bare FastMCP with a ping tool for testing."""
    auth = None
    if verifier is not None:
        auth = AuthSettings(
            issuer_url=AnyHttpUrl("https://auth.snodo.dev"),
            resource_server_url=AnyHttpUrl("https://mcp.snodo.dev"),
        )

    mcp = MCPServer("test-auth", token_verifier=verifier, auth=auth)

    @mcp.tool()
    def ping() -> str:
        return "pong"

    return mcp


def _valid_jrpc(body: dict) -> bytes:
    """Serialize a JSON-RPC request body."""
    return json.dumps(body).encode()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOAuthProtectedResource:
    """/.well-known/oauth-protected-resource endpoint."""

    def test_protected_resource_endpoint(self):
        """GET to /.well-known/oauth-protected-resource returns 200 with issuer URL."""

        class RejectAllVerifier(TokenVerifier):
            async def verify_token(self, token: str) -> Optional[AccessToken]:
                return None

        async def run():
            mcp = _make_mcp(verifier=RejectAllVerifier())
            app = mcp.streamable_http_app(
                stateless_http=True,
                transport_security=TransportSecuritySettings(
                    enable_dns_rebinding_protection=False, allowed_hosts=["*"],
                ),
            )
            async with anyio.create_task_group() as tg:
                mcp.session_manager._task_group = tg
                client = TestClient(app)
                resp = client.get("/.well-known/oauth-protected-resource")
                assert resp.status_code == 200, resp.text
                data = resp.json()
                assert "https://auth.snodo.dev/" in data["authorization_servers"]

        anyio.run(run)


class TestMCPEndpoint:
    """POST /mcp endpoint with authentication."""

    def test_mcp_endpoint_rejects_unauthenticated(self):
        """POST to /mcp without Authorization header returns 401."""

        class RejectAllVerifier(TokenVerifier):
            async def verify_token(self, token: str) -> Optional[AccessToken]:
                return None

        async def run():
            mcp = _make_mcp(verifier=RejectAllVerifier())
            app = mcp.streamable_http_app(
                stateless_http=True,
                transport_security=TransportSecuritySettings(
                    enable_dns_rebinding_protection=False, allowed_hosts=["*"],
                ),
            )
            async with anyio.create_task_group() as tg:
                mcp.session_manager._task_group = tg
                client = TestClient(app)
                resp = client.post(
                    "/mcp",
                    content=_valid_jrpc({"jsonrpc": "2.0", "method": "ping", "id": 1}),
                    headers={
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                )
                assert resp.status_code == 401, (
                    f"Expected 401, got {resp.status_code}: {resp.text[:200]}"
                )

        anyio.run(run)

    def test_mcp_endpoint_accepts_valid_token(self):
        """POST to /mcp with a valid Bearer JWT and JwksTokenVerifier returns 200."""
        mock_client = MagicMock(spec=JwksClient)
        mock_client.verify.return_value = {
            "sub": "test-user",
            "client_id": "test-client",
            "scopes": [],
            "exp": 9999999999,
        }

        verifier = JwksTokenVerifier(mock_client)

        async def run():
            mcp = _make_mcp(verifier=verifier)
            app = mcp.streamable_http_app(
                stateless_http=True,
                transport_security=TransportSecuritySettings(
                    enable_dns_rebinding_protection=False, allowed_hosts=["*"],
                ),
            )
            async with anyio.create_task_group() as tg:
                mcp.session_manager._task_group = tg
                client = TestClient(app)
                resp = client.post(
                    "/mcp",
                    content=_valid_jrpc({"jsonrpc": "2.0", "method": "ping", "id": 1}),
                    headers={
                        "Authorization": "Bearer valid-jwt",
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                )
                assert resp.status_code == 200, (
                    f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
                )

        anyio.run(run)


class TestMultiAudienceVerifier:
    def test_selects_only_the_current_hosts_audience(self):
        mock_client = MagicMock(spec=JwksClient)
        mock_client.verify.return_value = {"sub": "test-user"}
        verifier = JwksTokenVerifier(
            mock_client,
            audiences={"a.example": "https://a.example/mcp", "b.example": "https://b.example/mcp"},
        )

        async def run():
            with verifier.request_host("a.example"):
                assert await verifier.verify_token("token") is not None
            with verifier.request_host("b.example"):
                assert await verifier.verify_token("token") is not None
            assert await verifier.verify_token("token") is None

        anyio.run(run)
        assert [call.kwargs["audience"] for call in mock_client.verify.call_args_list] == [
            "https://a.example/mcp", "https://b.example/mcp"
        ]

    def test_tokens_are_rejected_on_the_other_hosts_audience(self):
        mock_client = MagicMock(spec=JwksClient)
        mock_client.verify.side_effect = lambda token, audience: (
            {"sub": "test-user"} if token == audience else None
        )
        verifier = JwksTokenVerifier(
            mock_client,
            audiences={"a.example": "aud-a", "b.example": "aud-b"},
        )

        async def run():
            with verifier.request_host("a.example"):
                assert await verifier.verify_token("aud-a") is not None
                assert await verifier.verify_token("aud-b") is None
            with verifier.request_host("b.example"):
                assert await verifier.verify_token("aud-b") is not None
                assert await verifier.verify_token("aud-a") is None

        anyio.run(run)

    def test_interleaved_requests_keep_their_host_audience(self):
        mock_client = MagicMock(spec=JwksClient)
        mock_client.verify.return_value = {"sub": "test-user"}
        verifier = JwksTokenVerifier(
            mock_client,
            audiences={"a.example": "aud-a", "b.example": "aud-b"},
        )

        async def run():
            a_ready = anyio.Event()
            b_ready = anyio.Event()

            async def verify_a():
                with verifier.request_host("a.example"):
                    a_ready.set()
                    await b_ready.wait()
                    assert await verifier.verify_token("token-a") is not None

            async def verify_b():
                with verifier.request_host("b.example"):
                    await a_ready.wait()
                    b_ready.set()
                    assert await verifier.verify_token("token-b") is not None

            async with anyio.create_task_group() as task_group:
                task_group.start_soon(verify_a)
                task_group.start_soon(verify_b)

        anyio.run(run)
        assert [call.kwargs["audience"] for call in mock_client.verify.call_args_list] == [
            "aud-b", "aud-a"
        ]

    def test_unknown_host_fails_closed(self):
        mock_client = MagicMock(spec=JwksClient)
        verifier = JwksTokenVerifier(mock_client, audiences={"a.example": "aud-a"})

        async def run():
            with verifier.request_host("other.example"):
                assert await verifier.verify_token("token") is None
            assert await verifier.verify_token("token") is None

        anyio.run(run)
        mock_client.verify.assert_not_called()
