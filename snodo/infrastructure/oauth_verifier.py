"""TokenVerifier that validates Bearer JWTs via JwksClient.

FILE: snodo/infrastructure/oauth_verifier.py
"""

from typing import Optional

from mcp.server.auth.provider import AccessToken, TokenVerifier

from snodo.infrastructure.jwks import JwksClient


class JwksTokenVerifier(TokenVerifier):
    """TokenVerifier that delegates JWT verification to a JwksClient.

    Implements the mcp.server.auth.provider.TokenVerifier protocol so that
    FastMCP can use it to protect its streamable-http transport.
    """

    def __init__(self, jwks_client: JwksClient):
        self._jwks = jwks_client

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        payload = self._jwks.verify(token)
        if payload is None:
            return None
        return AccessToken(
            token=token,
            client_id=payload.get("client_id", payload.get("sub", "unknown")),
            scopes=payload.get("scopes", []),
            expires_at=payload.get("exp"),
        )
