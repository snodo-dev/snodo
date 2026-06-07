"""Cloud command — snodo cloud connect / disconnect / status.

FILE: snodo/cli/commands/cloud_cmd.py
"""

import sys


def cloud_connect_command(api_key: str) -> int:
    """Store the snodo cloud API key and enable audit sync."""
    if not _validate_key_format(api_key):
        print(
            "Error: Invalid API key format. Expected prefix 'sndo_staging_' or 'sndo_live_'.",
            file=sys.stderr,
        )
        return 1

    from snodo.cli.config import ConfigManager

    mgr = ConfigManager()
    config = mgr.load()
    cloud = config.setdefault("cloud", {})
    cloud["api_key"] = api_key
    cloud["sync_enabled"] = True
    mgr.save(config)

    prefix = api_key[:16] + "..." if len(api_key) > 16 else api_key[:4] + "***"
    print("✓ Connected to snodo cloud.")
    print(f"  API key:  {prefix}")
    print("  Audit sync enabled.")
    return 0


def cloud_disconnect_command() -> int:
    """Clear the snodo cloud API key and disable sync."""
    from snodo.cli.config import ConfigManager

    mgr = ConfigManager()
    config = mgr.load()
    cloud = config.setdefault("cloud", {})
    cloud["api_key"] = ""
    cloud["sync_enabled"] = False
    mgr.save(config)

    print("Disconnected from snodo cloud.")
    return 0


def cloud_status_command() -> int:
    """Show cloud connection and sync state."""
    from snodo.cli.config import ConfigManager
    from snodo.infrastructure.cloud_sync import CloudSyncState

    mgr = ConfigManager()
    config = mgr.load()
    cloud = config.get("cloud", {}) if isinstance(config, dict) else {}

    api_key = cloud.get("api_key", "")
    sync_enabled = cloud.get("sync_enabled", False)
    api_url = cloud.get("api_url", "https://api.snodo.dev")

    if api_key:
        prefix = api_key[:16] + "..." if len(api_key) > 16 else api_key[:4] + "***"
        print("Snodo cloud: connected")
        print(f"  API key:    {prefix}")
        print(f"  API URL:    {api_url}")
        print(f"  Sync:       {'enabled' if sync_enabled else 'disabled'}")
    else:
        print("Snodo cloud: not connected")
        print("  Run: snodo cloud connect <api_key>")
        return 0

    state = CloudSyncState()
    summary = state.get_summary()
    if summary:
        print()
        print("Sync status per session:")
        for sid, info in sorted(summary.items()):
            seq = info.get("last_synced_sequence", 0)
            at = info.get("last_synced_at", 0)
            ts = _format_ts(at) if at else "never"
            print(f"  {sid}:  last_seq={seq}  synced_at={ts}")
    else:
        print()
        print("No sessions synced yet.")

    return 0


def _validate_key_format(key: str) -> bool:
    """Validate snodo cloud API key format."""
    return key.startswith("sndo_staging_") or key.startswith("sndo_live_")


def _format_ts(ts: float) -> str:
    """Format a unix timestamp for display."""
    import time as _time
    try:
        return _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(ts))
    except (ValueError, OSError):
        return "unknown"
