"""JWKS client for RS256 token verification."""

import json
import logging
import time
from typing import Callable, Optional

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

JWKS_URI = "https://mcp-auth.snodo.dev/.well-known/jwks.json"
JWKS_KID = "mcp-auth-rs256-v1"
OAUTH_ISSUER = "https://mcp-auth.snodo.dev"
_CACHE_TTL_SECONDS = 300
_logger = logging.getLogger(__name__)


class JwksClient:
    """Fetch and cache RS256 public keys, refreshing once for an unknown kid."""

    def __init__(self, uri: str = JWKS_URI, *, fetcher: Optional[Callable] = None,
                 cache_ttl: float = _CACHE_TTL_SECONDS):
        self._uri = uri
        self._fetcher = fetcher or (lambda url, timeout: httpx.get(url, timeout=timeout))
        self._cache_ttl = cache_ttl
        self._keys = {}
        self._cached_key = None  # Retained for compatibility with existing callers/tests.
        self._fetched_at = 0.0
        self._unknown_refresh_at = 0.0

    def fetch(self) -> bool:
        """Fetch and cache all valid RSA keys. Failures never expose response data."""
        try:
            response = self._fetcher(self._uri, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
                return False
            keys = {}
            for item in data["keys"]:
                if isinstance(item, dict) and isinstance(item.get("kid"), str):
                    try:
                        keys[item["kid"]] = RSAAlgorithm.from_jwk(json.dumps(item))
                    except Exception:
                        continue
            self._keys = keys
            self._cached_key = keys.get(JWKS_KID)
            self._fetched_at = time.monotonic()
            return bool(keys) and JWKS_KID in keys if self._uri == JWKS_URI else bool(keys)
        except Exception:
            _logger.warning("JWKS fetch failed")
            return False

    def verify(self, token: str, audience: Optional[str] = None, *, issuer: str = OAUTH_ISSUER,
               require_iat: bool = True) -> Optional[dict]:
        """Verify signed JWT claims; return None for every malformed or invalid token."""
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if not isinstance(kid, str):
                return None
            if time.monotonic() - self._fetched_at >= self._cache_ttl:
                self.fetch()
            key = self._keys.get(kid)
            if key is None and time.monotonic() - self._unknown_refresh_at >= self._cache_ttl:
                self._unknown_refresh_at = time.monotonic()
                self.fetch()
                key = self._keys.get(kid)
            if key is None:
                return None
            required = ["exp", "iss"] + (["iat"] if require_iat else [])
            return jwt.decode(token, key, algorithms=["RS256"], options={"require": required},
                              issuer=issuer, audience=audience)
        except jwt.ExpiredSignatureError:
            _logger.warning("MCP authentication refused: expired")
            return None
        except jwt.InvalidAudienceError:
            _logger.warning("MCP authentication refused: audience mismatch")
            return None
        except jwt.InvalidIssuerError:
            _logger.warning("MCP authentication refused: wrong issuer")
            return None
        except Exception:
            _logger.warning("JWT authentication refused")
            return None
