"""Resolve credentials for cloud sync without exposing OAuth secrets."""

from __future__ import annotations

import socket
import time
import logging

from snodo.infrastructure.cloud_oauth_store import CloudOAuthState, load_oauth_state, save_oauth_state

_logger = logging.getLogger(__name__)


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
            try:
                payload = client.refresh(state.client_id, state.refresh_token)
            except Exception:
                return None, True
            access = payload.get("access_token")
            refresh = payload.get("refresh_token") or state.refresh_token
            expires_in = payload.get("expires_in")
            if not isinstance(access, str) or not access or not isinstance(expires_in, (int, float)):
                return None, True
            state = CloudOAuthState(state.client_id, access, refresh, time.time() + float(expires_in), payload.get("scope") or state.scope)
            save_oauth_state(state)
        return state.access_token, True
    return _configured_api_key(config), False


def _configured_api_key(config: dict) -> str | None:
    cloud = config.get("cloud", {}) if isinstance(config, dict) else {}
    key = cloud.get("api_key") if isinstance(cloud, dict) else None
    return key.strip() or None if isinstance(key, str) else None


def cloud_identity_headers() -> dict[str, str]:
    from snodo.version import __version__
    return {"User-Agent": f"snodo/{__version__}", "X-Snodo-Device": cloud_device_name()}


def cloud_device_name() -> str:
    """Return the hostname used to identify this machine to the cloud."""
    return socket.gethostname()


def cloud_ingest_headers(lease_token: str) -> dict[str, str]:
    """Build headers for an ingest request using its admission lease."""
    return {
        "Authorization": f"Bearer {lease_token}",
        "Content-Type": "application/json",
        **cloud_identity_headers(),
    }


def cloud_sync_enabled(config: dict | None = None) -> bool:
    """Return whether cloud sync is enabled and has OAuth or API-key credentials."""
    if config is None:
        from snodo.config import ConfigManager
        config = ConfigManager().load()
    cloud = config.get("cloud", {}) if isinstance(config, dict) else {}
    if not cloud.get("sync_enabled"):
        return False
    state = load_oauth_state()
    api_key = cloud.get("api_key", "")
    return bool(state.access_token or state.refresh_token) or bool(
        isinstance(api_key, str) and api_key.strip()
    )


def safe_cloud_sync_credential(config: dict, *, force_refresh: bool = False, http_client=None) -> tuple[str | None, bool]:
    """Resolve a sync credential, reporting refresh failures without secrets."""
    try:
        credential, is_oauth = resolve_cloud_credential(config, force_refresh=force_refresh, http_client=http_client)
        if credential is None and is_oauth:
            credential = _configured_api_key(config)
            if credential:
                _logger.warning("Cloud OAuth login needs `snodo cloud login` again; using the configured API key for cloud sync")
                return credential, False
            _logger.warning("Cloud OAuth credential refresh failed; run `snodo cloud login` again")
        return credential, is_oauth
    except Exception:
        key = _configured_api_key(config)
        if key:
            _logger.warning("Cloud OAuth login needs `snodo cloud login` again; using the configured API key for cloud sync")
            return key, False
        _logger.warning("Cloud OAuth credential refresh failed; run `snodo cloud login` again")
        return None, True
