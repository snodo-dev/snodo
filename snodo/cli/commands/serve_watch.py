"""Configuration for browser links returned by watch_job."""

import os
import re


def watch_link_settings(transport: str, tunnel_hostname: str | None) -> tuple[str, int]:
    """Return a reachable public origin and configured capability lifetime."""
    base_url = (
        os.environ.get("SNODO_PUBLIC_BASE_URL", "").strip().rstrip("/")
        if transport != "stdio" else ""
    )
    if not base_url and tunnel_hostname:
        base_url = f"https://{tunnel_hostname}"
    if base_url:
        base_url = re.sub(r"/mcp$", "", base_url)
    ttl = int(os.environ.get("SNODO_WATCH_LINK_TTL", str(24 * 60 * 60)))
    if ttl <= 0:
        raise ValueError("SNODO_WATCH_LINK_TTL must be positive")
    return base_url, ttl


def cloud_live_view_url() -> str | None:
    """Return the configured cloud live-view host when sync is enabled."""
    from snodo.config import ConfigManager, get_cloud_liveness_url
    from snodo.infrastructure.cloud_credentials import cloud_sync_enabled

    config = ConfigManager().load()
    return get_cloud_liveness_url(config) if cloud_sync_enabled(config) else None
