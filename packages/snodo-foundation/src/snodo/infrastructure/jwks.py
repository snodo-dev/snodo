"""Standalone JWKS client that fetches RS256 public keys from MCP Auth.

FILE: snodo/infrastructure/jwks.py
"""

import json
import sys
from typing import Optional

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

JWKS_URI = "https://mcp-auth.snodo.dev/.well-known/jwks.json"
JWKS_KID = "mcp-auth-rs256-v1"
OAUTH_ISSUER = "https://mcp-auth.snodo.dev"


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

    def verify(self, token: str) -> Optional[dict]:
        """Verify a Bearer JWT against the cached RS256 public key.

        Checks header.kid matches JWKS_KID, then verifies the signature,
        expiry, and issued-at time. Returns the decoded payload dict on
        success, or None on any failure.
        """
        if self._cached_key is None:
            return None
        try:
            header = jwt.get_unverified_header(token)
            if header.get("kid") != JWKS_KID:
                return None
            payload = jwt.decode(
                token,
                self._cached_key,
                algorithms=["RS256"],
                options={"require": ["exp", "iat", "iss"]},
                issuer=OAUTH_ISSUER,
            )
            return payload
        except jwt.InvalidTokenError:
            return None
