"""Tests for FastMCP OAuth 2.1 authentication integration.

FILE: tests/mcp/test_auth.py
"""

import json
from typing import Optional
from unittest.mock import MagicMock

import anyio
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.testclient import TestClient

from snodo.infrastructure.jwks import JwksClient
from snodo.infrastructure.oauth_verifier import JwksTokenVerifier


def _make_mcp(
    verifier: Optional[TokenVerifier] = None,
) -> FastMCP:
    """Create a bare FastMCP with a ping tool for testing."""
    auth = None
    if verifier is not None:
        auth = AuthSettings(
            issuer_url=AnyHttpUrl("https://auth.snodo.dev"),
            resource_server_url=AnyHttpUrl("https://mcp.snodo.dev"),
        )

    mcp = FastMCP(
        "test-auth",
        token_verifier=verifier,
        auth=auth,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
            allowed_hosts=["*"],
        ),
    )

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
            app = mcp.streamable_http_app()
            async with anyio.create_task_group() as tg:
                mcp._session_manager._task_group = tg
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
            app = mcp.streamable_http_app()
            async with anyio.create_task_group() as tg:
                mcp._session_manager._task_group = tg
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
            app = mcp.streamable_http_app()
            async with anyio.create_task_group() as tg:
                mcp._session_manager._task_group = tg
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
