"""Tests for JWKS client (infrastructure/jwks.py).

FILE: tests/infrastructure/test_jwks.py
"""

import base64
import time
from unittest.mock import MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa

from snodo.infrastructure.jwks import JwksClient, JWKS_KID, OAUTH_ISSUER


def _b64url(n: int) -> str:
    """Convert an integer to a base64url-encoded string without padding."""
    num_bytes = (n.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(n.to_bytes(num_bytes, "big")).rstrip(b"=").decode("ascii")


@pytest.fixture
def rsa_keypair():
    """Generate a throwaway RSA 2048-bit keypair for tests."""
    private = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    return private, private.public_key()


@pytest.fixture
def jwk_dict(rsa_keypair):
    """Build a JWK dict from the test RSA public key's modulus and exponent."""
    _, pub = rsa_keypair
    nums = pub.public_numbers()
    return {
        "kty": "RSA",
        "n": _b64url(nums.n),
        "e": _b64url(nums.e),
        "alg": "RS256",
        "kid": JWKS_KID,
    }


@pytest.fixture
def jwks_response(jwk_dict):
    """Build a full JWKS response dict containing the test JWK."""
    return {"keys": [jwk_dict]}


# ---------------------------------------------------------------------------
# fetch()
# ---------------------------------------------------------------------------


class TestFetch:
    def test_fetch_success(self, jwks_response):
        """Valid JWKS response caches a non-None public key."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = jwks_response
        mock_resp.raise_for_status.return_value = None

        client = JwksClient()
        with patch("httpx.get", return_value=mock_resp):
            result = client.fetch()

        assert result is True
        assert client._cached_key is not None

    def test_fetch_network_error(self):
        """httpx raises -> fetch() returns False."""
        client = JwksClient()
        with patch("httpx.get", side_effect=Exception("connection refused")):
            result = client.fetch()

        assert result is False
        assert client._cached_key is None

    def test_fetch_http_error(self):
        """500 response -> fetch() returns False."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("500 Server Error")

        client = JwksClient()
        with patch("httpx.get", return_value=mock_resp):
            result = client.fetch()

        assert result is False
        assert client._cached_key is None

    def test_fetch_wrong_kid(self):
        """JWKS response has keys but none match -> fetch() returns False."""
        wrong_key = {
            "kty": "RSA",
            "n": "abc",
            "e": "AQAB",
            "kid": "other-key-v2",
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"keys": [wrong_key]}
        mock_resp.raise_for_status.return_value = None

        client = JwksClient()
        with patch("httpx.get", return_value=mock_resp):
            result = client.fetch()

        assert result is False
        assert client._cached_key is None


# ---------------------------------------------------------------------------
# verify()
# ---------------------------------------------------------------------------


class TestVerify:
    def test_verify_valid_token(self, rsa_keypair, jwks_response):
        """Issue a real RS256 JWT, verify returns decoded payload with correct sub."""
        private, _ = rsa_keypair
        now = int(time.time())
        payload = {"sub": "test-user", "iss": OAUTH_ISSUER, "exp": now + 3600, "iat": now}

        token = jwt.encode(payload, private, algorithm="RS256", headers={"kid": JWKS_KID})

        mock_resp = MagicMock()
        mock_resp.json.return_value = jwks_response
        mock_resp.raise_for_status.return_value = None

        client = JwksClient()
        with patch("httpx.get", return_value=mock_resp):
            client.fetch()

        result = client.verify(token)
        assert result is not None
        assert result["sub"] == "test-user"

    def test_verify_expired_token(self, rsa_keypair, jwks_response):
        """JWT with exp in the past -> verify returns None."""
        private, _ = rsa_keypair
        now = int(time.time())
        payload = {"sub": "test-user", "iss": OAUTH_ISSUER, "exp": now - 3600, "iat": now}

        token = jwt.encode(payload, private, algorithm="RS256", headers={"kid": JWKS_KID})

        mock_resp = MagicMock()
        mock_resp.json.return_value = jwks_response
        mock_resp.raise_for_status.return_value = None

        client = JwksClient()
        with patch("httpx.get", return_value=mock_resp):
            client.fetch()

        result = client.verify(token)
        assert result is None

    def test_verify_tampered_token(self, rsa_keypair, jwks_response):
        """Flip a byte in the JWT payload -> verify returns None."""
        private, _ = rsa_keypair
        now = int(time.time())
        payload = {"sub": "test-user", "iss": OAUTH_ISSUER, "exp": now + 3600, "iat": now}

        token = jwt.encode(payload, private, algorithm="RS256", headers={"kid": JWKS_KID})

        parts = token.split(".")
        # Decode payload bytes, flip one byte, re-encode
        payload_bytes = base64.urlsafe_b64decode(parts[1] + "==")
        mangled = bytearray(payload_bytes)
        mangled[0] ^= 0x01
        tampered_payload = base64.urlsafe_b64encode(bytes(mangled)).rstrip(b"=").decode()
        tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"

        mock_resp = MagicMock()
        mock_resp.json.return_value = jwks_response
        mock_resp.raise_for_status.return_value = None

        client = JwksClient()
        with patch("httpx.get", return_value=mock_resp):
            client.fetch()

        result = client.verify(tampered_token)
        assert result is None

    def test_verify_wrong_issuer(self, rsa_keypair, jwks_response):
        """Token with wrong iss claim -> verify returns None."""
        private, _ = rsa_keypair
        now = int(time.time())
        payload = {"sub": "test-user", "iss": "https://evil.example.com",
                   "exp": now + 3600, "iat": now}

        token = jwt.encode(payload, private, algorithm="RS256", headers={"kid": JWKS_KID})

        mock_resp = MagicMock()
        mock_resp.json.return_value = jwks_response
        mock_resp.raise_for_status.return_value = None

        client = JwksClient()
        with patch("httpx.get", return_value=mock_resp):
            client.fetch()

        result = client.verify(token)
        assert result is None

    def test_verify_no_key_cached(self, rsa_keypair):
        """Call verify before fetch -> None (no key cached)."""
        private, _ = rsa_keypair
        now = int(time.time())
        payload = {"sub": "test-user", "iss": OAUTH_ISSUER, "exp": now + 3600, "iat": now}

        token = jwt.encode(payload, private, algorithm="RS256", headers={"kid": JWKS_KID})

        client = JwksClient()
        result = client.verify(token)
        assert result is None
