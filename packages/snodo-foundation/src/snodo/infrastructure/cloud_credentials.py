"""Resolve credentials for cloud sync without exposing OAuth secrets."""

from __future__ import annotations

import socket
import time

from snodo.infrastructure.cloud_oauth_store import CloudOAuthState, load_oauth_state, save_oauth_state


def resolve_cloud_credential(config: dict, *, force_refresh: bool = False, http_client=None) -> tuple[str | None, bool]:
    """Return (credential, is_oauth); OAuth takes precedence over API keys."""
    state = load_oauth_state()
    if state.client_id and (state.access_token or state.refresh_token):
        if force_refresh or state.is_near_expiry():
            if not state.has_refresh_token():
                return None, True
            from snodo.infrastructure.cloud_oauth_client import CloudOAuthClient
            from snodo.version import __version__

            client = CloudOAuthClient(config, http_client=http_client, version=__version__)
            payload = client.refresh(state.client_id, state.refresh_token)
            access = payload.get("access_token")
            refresh = payload.get("refresh_token") or state.refresh_token
            expires_in = payload.get("expires_in")
            if not isinstance(access, str) or not access or not isinstance(expires_in, (int, float)):
                return None, True
            state = CloudOAuthState(state.client_id, access, refresh, time.time() + float(expires_in), payload.get("scope") or state.scope)
            save_oauth_state(state)
        return state.access_token, True
    cloud = config.get("cloud", {}) if isinstance(config, dict) else {}
    key = cloud.get("api_key") if isinstance(cloud, dict) else None
    return (key.strip() or None, False) if isinstance(key, str) else (None, False)


def cloud_identity_headers() -> dict[str, str]:
    from snodo.version import __version__
    return {"User-Agent": f"snodo/{__version__}", "X-Snodo-Device": socket.gethostname()}
