"""TokenVerifier that validates Bearer JWTs via JwksClient.

FILE: snodo/infrastructure/oauth_verifier.py
"""

from contextvars import ContextVar, Token
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

from mcp.server.auth.provider import AccessToken, TokenVerifier

from snodo.infrastructure.jwks import JwksClient


class JwksTokenVerifier(TokenVerifier):
    """TokenVerifier that delegates JWT verification to a JwksClient.

    Implements the mcp.server.auth.provider.TokenVerifier protocol so that
    FastMCP can use it to protect its streamable-http transport.
    """

    def __init__(
        self,
        jwks_client: JwksClient,
        resource: Optional[str] = None,
        audiences: Optional[Dict[str, str]] = None,
    ):
        """
        Args:
            jwks_client: Fetches and caches the issuer's RS256 public key.
            resource: This server's resource identifier — the same value the
                protected-resource metadata advertises. Tokens are issued with
                it as their ``aud`` (RFC 8707), and it must be supplied here or
                verification rejects them.
        """
        self._jwks = jwks_client
        self._resource = resource
        self._audiences = dict(audiences) if audiences is not None else None
        self._request_host: ContextVar[Optional[str]] = ContextVar(
            f"oauth_verifier_request_host_{id(self)}", default=None
        )

    @contextmanager
    def request_host(self, host: str) -> Iterator[None]:
        """Select the audience for verification within the current request context."""
        token: Token[Optional[str]] = self._request_host.set(host)
        try:
            yield
        finally:
            self._request_host.reset(token)

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        if self._audiences is not None:
            host = self._request_host.get()
            if host is None or host not in self._audiences:
                return None
            audience = self._audiences[host]
        else:
            audience = self._resource
        payload = self._jwks.verify(token, audience=audience)
        if payload is None:
            return None
        return AccessToken(
            token=token,
            client_id=payload.get("client_id", payload.get("sub", "unknown")),
            scopes=payload.get("scopes", []),
            expires_at=payload.get("exp"),
            resource=payload.get("aud") if isinstance(payload.get("aud"), str) else None,
        )
