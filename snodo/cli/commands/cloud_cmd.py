"""Cloud command — snodo cloud connect / disconnect / status / sync.

FILE: snodo/cli/commands/cloud_cmd.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import typer

# ---------------------------------------------------------------------------
# Self-registering Typer app (discovered by snodo/cli/main.py discovery loop)
# ---------------------------------------------------------------------------

COMMAND_NAME = "cloud"

app = typer.Typer(invoke_without_command=True, help="Manage snodo cloud connection and audit sync")


@app.callback()
def _cloud_callback(ctx: typer.Context):
    """Manage snodo cloud connection and audit sync."""
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


@app.command(name="connect")
def cloud_connect(
    api_key: str = typer.Argument(..., help="Snodo cloud API key (starts with sndo_staging_ or sndo_live_)"),
):
    """Connect to snodo cloud and enable audit sync."""
    return cloud_connect_command(api_key)


@app.command(name="disconnect")
def cloud_disconnect():
    """Disconnect from snodo cloud and disable sync."""
    return cloud_disconnect_command()


@app.command(name="status")
def cloud_status():
    """Show cloud connection and sync status."""
    return cloud_status_command()


@app.command(name="sync")
def cloud_sync(
    sync_all: bool = typer.Option(False, "--all", help="Sync all sessions for the current project"),
    session: str = typer.Option("", "--session", help="Sync a specific session by ID"),
):
    """Ship unsynced audit events to snodo cloud."""
    return cloud_sync_command(sync_all=sync_all, session_id=session)



def cloud_connect_command(api_key: str) -> int:
    """Store the snodo cloud API key and enable audit sync."""
    if not _validate_key_format(api_key):
        print(
            "Error: Invalid API key format. Expected prefix 'sndo_staging_' or 'sndo_live_'.",
            file=sys.stderr,
        )
        return 1

    from snodo.config import ConfigManager

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
    from snodo.config import ConfigManager

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
    from snodo.config import ConfigManager
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


def cloud_sync_command(sync_all: bool = False, session_id: str = "") -> int:
    """Sync audit events to snodo cloud for one or more sessions.

    --all: sync all sessions for the current project
    --session <id>: sync a specific session
    (no flags): sync the current active session
    """
    from snodo.config import ConfigManager
    from snodo.infrastructure.paths import require_project_root
    from snodo.infrastructure.cloud_sync import CloudSyncDispatcher
    from snodo.infrastructure.audit import AuditLog

    mgr = ConfigManager()
    config = mgr.load()
    cloud = config.get("cloud", {}) if isinstance(config, dict) else {}

    api_key = cloud.get("api_key", "")
    api_url = cloud.get("api_url", "https://api.snodo.dev")

    if not api_key:
        print("Error: Not connected to snodo cloud.", file=sys.stderr)
        print("  Run: snodo cloud connect <api_key>", file=sys.stderr)
        return 1

    project_root = require_project_root()

    from snodo.infrastructure.session import SessionManager
    from snodo.infrastructure.state import read_state

    session_mgr = SessionManager()

    # Resolve which sessions to sync
    sessions_to_sync: list = []

    if session_id:
        try:
            session = session_mgr.load_session(session_id)
        except FileNotFoundError:
            print(f"Error: Session not found: {session_id}", file=sys.stderr)
            return 1
        sessions_to_sync = [session]

    elif sync_all:
        sessions_to_sync = session_mgr.list_sessions(project_root=project_root)

    else:
        # Active session for current mode
        state = read_state(project_root)
        mode = state.current_mode
        if not mode:
            print("Error: No active mode set. Run 'snodo mode change <mode>' first.",
                  file=sys.stderr)
            return 1
        session = session_mgr.get_active_session(mode, project_root)
        if session is None:
            print(f"Error: No active session for mode={mode}", file=sys.stderr)
            return 1
        sessions_to_sync = [session]

    if not sessions_to_sync:
        print("No sessions to sync.")
        return 0

    dispatcher = CloudSyncDispatcher()
    total_synced = 0
    total_failed = 0

    for session in sessions_to_sync:
        sid = session.session_id
        proot = session.project_root

        audit_path = str(Path(proot) / ".snodo" / "audit.log")
        audit_log = AuditLog(audit_path)

        result = dispatcher.sync(sid, proot, audit_log, api_key, api_url)

        if result["synced"] > 0:
            print(f"  {sid}  ✓ {result['synced']} events synced")
            total_synced += result["synced"]
        elif result.get("failed"):
            print(f"  {sid}  ✗ sync failed")
            total_failed += 1
        else:
            print(f"  {sid}  — no new events")

    if total_synced > 0 or total_failed > 0:
        print()
        print(f"Synced {total_synced} events across {len(sessions_to_sync)} session(s).")
        if total_failed:
            print(f"  {total_failed} session(s) had failures.")

    return 0 if total_failed == 0 else 1
