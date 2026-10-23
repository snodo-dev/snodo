"""Cloudflare Access assertion verification, using generated signing keys only."""

import base64
import time
from unittest.mock import MagicMock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from snodo.infrastructure.access_assertion import AccessAssertionVerifier
from snodo.infrastructure.jwks import JwksClient


def _jwk(public_key, kid):
    numbers = public_key.public_numbers()

    def b64(value):
        return base64.urlsafe_b64encode(value.to_bytes((value.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()

    return {"kty": "RSA", "n": b64(numbers.n), "e": b64(numbers.e), "alg": "RS256", "kid": kid}


def test_access_assertion_verification():
    key1 = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key2 = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    domain, aud = "team.cloudflareaccess.com", "expected-aud"
    calls = []
    current_keys = [_jwk(key1.public_key(), "one")]

    def fetcher(url, timeout):
        calls.append(url)
        response = MagicMock()
        response.json.return_value = {"keys": list(current_keys)}
        response.raise_for_status.return_value = None
        return response

    client = JwksClient(f"https://{domain}/cdn-cgi/access/certs", fetcher=fetcher, cache_ttl=0.01)
    verifier = AccessAssertionVerifier(domain, aud, jwks_client=client)
    assert client.fetch()
    now = int(time.time())

    def token(key, kid="one", **claims):
        payload = {"iss": f"https://{domain}", "aud": aud, "exp": now + 300, **claims}
        return jwt.encode(payload, key, algorithm="RS256", headers={"kid": kid})

    assert verifier.verify(token(key1))
    assert not verifier.verify(token(key1, aud="wrong"))
    assert not verifier.verify(token(key1, iss="https://wrong.example"))
    assert not verifier.verify(jwt.encode({"iss": f"https://{domain}", "aud": aud, "exp": now - 1}, key1, algorithm="RS256", headers={"kid": "one"}))
    assert not verifier.verify(token(key2))

    unknown = token(key2, kid="new")
    before = len(calls)
    assert not verifier.verify(unknown)
    assert len(calls) == before + 1
    assert not verifier.verify(unknown)
    assert len(calls) == before + 1

    time.sleep(0.02)
    current_keys[:] = [_jwk(key2.public_key(), "new")]
    rotated = token(key2, kid="new")
    assert verifier.verify(rotated)
    assert len(calls) == before + 2


def test_access_fetch_failure_and_bad_headers_fail_closed():
    def fail(url, timeout):
        raise OSError("private fetch detail")

    verifier = AccessAssertionVerifier("team.cloudflareaccess.com", "aud",
                                       jwks_client=JwksClient(fetcher=fail))
    assert not verifier.verify(None)
    assert not verifier.verify("")
    assert not verifier.verify("not-a-jwt")
    assert not verifier.verify("x" * 20000)
    assert not verifier.verify("eyJhbGciOiJSUzI1NiIsImtpZCI6Im5vbmUifQ.e30.sig")
