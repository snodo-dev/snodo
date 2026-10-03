"""Protocol helpers for Snodo's public cloud OAuth client."""

from __future__ import annotations

import base64
import hashlib
import secrets
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from snodo.config import get_cloud_oauth_metadata_url

USER_AGENT = "snodo/0.19.3"
_REQUIRED_ENDPOINTS = (
    "authorization_endpoint",
    "token_endpoint",
    "registration_endpoint",
    "jwks_uri",
)


class CloudOAuthError(RuntimeError):
    """Safe, credential-free OAuth protocol failure."""


@dataclass(frozen=True)
class OAuthMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str
    jwks_uri: str


def pkce_verifier() -> str:
    """Create an RFC 7636 verifier."""
    return secrets.token_urlsafe(64).rstrip("=")


def pkce_challenge(verifier: str) -> str:
    """Return the S256 challenge for a verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def oauth_state() -> str:
    """Create an unpredictable OAuth state value."""
    return secrets.token_urlsafe(32)


class CloudOAuthClient:
    """Discover and communicate with the cloud OAuth authorization server.

    Inject ``httpx.Client`` (or a compatible client) to use a stub transport.
    The client owns no filesystem state and never logs response/request data.
    """

    def __init__(
        self,
        config: dict | None = None,
        *,
        http_client: Any | None = None,
        version: str = "0.19.3",
        hostname: str | None = None,
    ) -> None:
        self.metadata_url = get_cloud_oauth_metadata_url(config or {})
        self.http = http_client or httpx.Client(timeout=15.0)
        self.user_agent = f"snodo/{version}"
        self.hostname = hostname or socket.gethostname()
        self.metadata: OAuthMetadata | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent, "X-Snodo-Device": self.hostname}

    def discover(self) -> OAuthMetadata:
        if not self.metadata_url.startswith("https://"):
            raise CloudOAuthError("OAuth metadata URL must use HTTPS")
        payload = self._json_request("GET", self.metadata_url)
        values: dict[str, str] = {}
        for name in _REQUIRED_ENDPOINTS:
            value = payload.get(name)
            if not isinstance(value, str) or not value:
                raise CloudOAuthError(f"OAuth metadata is missing required endpoint: {name}")
            if not value.startswith("https://"):
                raise CloudOAuthError(f"OAuth metadata endpoint {name} must use HTTPS")
            values[name] = value
        issuer = payload.get("issuer", "")
        if not isinstance(issuer, str) or not issuer.startswith("https://"):
            raise CloudOAuthError("OAuth metadata issuer must use HTTPS")
        self.metadata = OAuthMetadata(issuer=issuer, **values)
        return self.metadata

    def register_client(self, redirect_uri: str = "http://localhost:*") -> str:
        metadata = self._require_metadata()
        payload = self._json_request(
            "POST",
            metadata.registration_endpoint,
            json={
                "client_name": "Snodo",
                "redirect_uris": [redirect_uri],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            },
        )
        client_id = payload.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise CloudOAuthError("OAuth client registration response has no client_id")
        return client_id

    def authorization_url(
        self,
        client_id: str,
        redirect_uri: str,
        verifier: str,
        state: str,
    ) -> str:
        metadata = self._require_metadata()
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": "cli",
                "state": state,
                "code_challenge": pkce_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        return f"{metadata.authorization_endpoint}?{query}"

    def exchange_code(self, client_id: str, code: str, redirect_uri: str, verifier: str) -> dict:
        metadata = self._require_metadata()
        return self._token_request(
            metadata.token_endpoint,
            {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )

    def refresh(self, client_id: str, refresh_token: str) -> dict:
        metadata = self._require_metadata()
        return self._token_request(
            metadata.token_endpoint,
            {"grant_type": "refresh_token", "client_id": client_id, "refresh_token": refresh_token},
        )

    def _token_request(self, endpoint: str, data: dict[str, str]) -> dict:
        try:
            response = self.http.post(endpoint, data=data, headers=self.headers)
            return self._decode_response(response)
        except CloudOAuthError:
            raise
        except Exception:
            raise CloudOAuthError("OAuth token request failed due to a network error") from None

    def _json_request(self, method: str, url: str, **kwargs: Any) -> dict:
        try:
            response = self.http.request(method, url, headers=self.headers, **kwargs)
            return self._decode_response(response)
        except CloudOAuthError:
            raise
        except Exception:
            raise CloudOAuthError("OAuth request failed due to a network error") from None

    @staticmethod
    def _decode_response(response: Any) -> dict:
        if not 200 <= response.status_code < 300:
            raise CloudOAuthError(f"OAuth endpoint returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except Exception:
            raise CloudOAuthError("OAuth endpoint returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise CloudOAuthError("OAuth endpoint returned an invalid response")
        return payload

    def _require_metadata(self) -> OAuthMetadata:
        if self.metadata is None:
            return self.discover()
        return self.metadata
