"""Verifier for Cloudflare Access JWT assertion headers."""

from typing import Optional

from snodo.infrastructure.jwks import JwksClient

ACCESS_ASSERTION_HEADER = "Cf-Access-Jwt-Assertion"


class AccessAssertionVerifier:
    """Verify a Cloudflare Access assertion against the team's published keys."""

    def __init__(self, access_team_domain: str, access_aud: str, *, jwks_client: Optional[JwksClient] = None):
        if (not isinstance(access_team_domain, str) or not access_team_domain
                or "://" in access_team_domain or "/" in access_team_domain):
            raise ValueError("invalid Access team domain")
        if not isinstance(access_aud, str) or not access_aud:
            raise ValueError("invalid Access audience")
        self._issuer = f"https://{access_team_domain}"
        self._audience = access_aud
        self._jwks = jwks_client or JwksClient(f"{self._issuer}/cdn-cgi/access/certs")

    def verify(self, assertion: Optional[str]) -> bool:
        """Return true only when the header contains a valid Access JWT."""
        if not isinstance(assertion, str) or not assertion or len(assertion) > 16384:
            return False
        return self._jwks.verify(assertion, audience=self._audience, issuer=self._issuer,
                                  require_iat=False) is not None
