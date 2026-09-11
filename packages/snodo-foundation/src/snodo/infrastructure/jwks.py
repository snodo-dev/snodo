"""Standalone JWKS client that fetches RS256 public keys from MCP Auth.

FILE: snodo/infrastructure/jwks.py
"""

import json
import sys
from typing import Optional

import logging

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

JWKS_URI = "https://mcp-auth.snodo.dev/.well-known/jwks.json"
JWKS_KID = "mcp-auth-rs256-v1"
OAUTH_ISSUER = "https://mcp-auth.snodo.dev"

_logger = logging.getLogger(__name__)


class JwksClient:
    """Fetches and caches RS256 public keys from a JWKS endpoint.

    Usage:
        client = JwksClient()
        if client.fetch():
            payload = client.verify(token)
    """

    def __init__(self):
        self._cached_key = None

    def fetch(self) -> bool:
        """Fetch JWKS, find the key with kid=JWKS_KID, and cache it.

        Returns True on success, False on failure (prints warning to stderr).
        """
        try:
            resp = httpx.get(JWKS_URI, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
            keys = data.get("keys", [])
            for key_data in keys:
                if key_data.get("kid") == JWKS_KID:
                    self._cached_key = RSAAlgorithm.from_jwk(json.dumps(key_data))
                    return True
            print(f"Warning: JWKS contains no key with kid={JWKS_KID!r}", file=sys.stderr)
            return False
        except Exception as exc:
            print(f"Warning: JWKS fetch failed: {exc}", file=sys.stderr)
            return False

    def verify(self, token: str, audience: Optional[str] = None) -> Optional[dict]:
        """Verify a Bearer JWT against the cached RS256 public key.

        Checks header.kid matches JWKS_KID, then verifies the signature,
        expiry, issued-at time, issuer and audience. Returns the decoded
        payload dict on success, or None on any failure.

        ``audience`` is required whenever the issuer stamps an ``aud`` claim,
        which it does for every resource-indicated token (RFC 8707/9068).
        PyJWT does not ignore an audience it was not told about: a token
        carrying ``aud`` with no ``audience`` argument raises
        InvalidAudienceError (api_jwt.py ``_validate_aud``). Omitting it
        therefore rejects every correctly-issued token rather than skipping
        the check.
        """
        if self._cached_key is None:
            return None
        try:
            header = jwt.get_unverified_header(token)
            if header.get("kid") != JWKS_KID:
                _logger.debug("token rejected: kid %r != %r", header.get("kid"), JWKS_KID)
                return None
            payload = jwt.decode(
                token,
                self._cached_key,
                algorithms=["RS256"],
                options={"require": ["exp", "iat", "iss"]},
                issuer=OAUTH_ISSUER,
                audience=audience,
            )
            return payload
        except jwt.InvalidTokenError as exc:
            # Every rejection reason collapsed to None here, which made a
            # correctly-issued token indistinguishable from a forged one.
            _logger.debug("token rejected: %s: %s", type(exc).__name__, exc)
            return None
