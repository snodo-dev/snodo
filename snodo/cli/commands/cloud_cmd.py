"""Cloud command — snodo cloud connect / disconnect / status / sync.

FILE: snodo/cli/commands/cloud_cmd.py
"""

import json
import sys
import time
from pathlib import Path

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


@app.command(name="login")
def cloud_login(no_browser: bool = typer.Option(False, "--no-browser", help="Print the sign-in URL without opening a browser")):
    """Sign in to snodo cloud in a browser."""
    return cloud_login_command(no_browser=no_browser)


@app.command(name="disconnect")
def cloud_disconnect():
    """Disconnect from snodo cloud and disable sync."""
    return cloud_disconnect_command()


@app.command(name="logout")
def cloud_logout():
    """Sign out of snodo cloud and clear stored OAuth tokens."""
    return cloud_logout_command()


@app.command(name="status")
def cloud_status():
    """Show cloud connection and sync status."""
    return cloud_status_command()


@app.command(name="sync")
def cloud_sync(
    sync_all: bool = typer.Option(False, "--all", help="Sync all sessions for the current project"),
    session: str = typer.Option("", "--session", help="Sync a specific session by ID"),
    force: bool = typer.Option(False, "--force", "--retry", help="Force re-attempt sync for refused sessions"),
):
    """Ship unsynced audit events to snodo cloud."""
    return cloud_sync_command(sync_all=sync_all, session_id=session, force=force)


@app.command(name="schema")
def cloud_schema(
    json_output: bool = typer.Option(
        False, "--json", help="Emit the cloud interface as JSON",
    ),
):
    """Publish the versioned engine-to-cloud payload schemas."""
    return cloud_schema_command(json_output=json_output)


def cloud_schema_command(json_output: bool = True) -> int:
    """Print the current schemas for the two cloud wire payloads."""
    from pydantic import TypeAdapter

    from snodo.infrastructure.cloud_interface import CLOUD_INTERFACE_VERSION
    from snodo.infrastructure.cloud_interface import CLOUD_INTERFACE_V5, CLOUD_INTERFACE_V6, CLOUD_INTERFACE_V7
    from snodo.infrastructure.cloud_liveness import LivenessSnapshot, LivenessSnapshotV6
    from snodo.infrastructure.cloud_runs import run_record_payload_schema
    from snodo.infrastructure.cloud_sync import AuditIngestBatch, AuditIngestBatchV6, AuditIngestBatchV7

    publication = {
        "interface_version": CLOUD_INTERFACE_VERSION,
        "payloads": {
            "cloud_ingest": TypeAdapter(AuditIngestBatchV7).json_schema(),
            "cloud_liveness": TypeAdapter(LivenessSnapshotV6).json_schema(),
            "run_record": run_record_payload_schema(),
        },
        "payloads_by_version": {
            str(CLOUD_INTERFACE_V5): {
                "cloud_ingest": TypeAdapter(AuditIngestBatch).json_schema(),
                "cloud_liveness": TypeAdapter(LivenessSnapshot).json_schema(),
            },
            str(CLOUD_INTERFACE_V6): {
                "cloud_ingest": TypeAdapter(AuditIngestBatchV6).json_schema(),
                "cloud_liveness": TypeAdapter(LivenessSnapshotV6).json_schema(),
            },
            str(CLOUD_INTERFACE_V7): {
                "cloud_ingest": TypeAdapter(AuditIngestBatchV7).json_schema(),
                "cloud_liveness": TypeAdapter(LivenessSnapshotV6).json_schema(),
            },
        },
    }
    print(json.dumps(publication, indent=2, sort_keys=True))
    return 0


def cloud_connect_command(api_key: str) -> int:
    """Store the API key, replacing any active OAuth login, and enable sync."""
    if not _validate_key_format(api_key):
        print(
            "Error: Invalid API key format. Expected prefix 'sndo_staging_' or 'sndo_live_'.",
            file=sys.stderr,
        )
        return 1

    from snodo.config import ConfigManager
    from snodo.infrastructure.cloud_oauth_store import load_oauth_state

    oauth = load_oauth_state()
    replacing_oauth = bool(oauth.access_token or oauth.refresh_token)
    mgr = ConfigManager()
    mgr.set_value(("cloud", "api_key"), api_key)
    mgr.set_value(("cloud", "sync_enabled"), True)

    if replacing_oauth:
        # Reuse logout's best-effort revocation and unconditional local cleanup.
        cloud_logout_command()

    print("✓ Connected to snodo cloud.")
    print("  Audit sync enabled.")
    if replacing_oauth:
        print("  API key authentication is now in use.")
    return 0


def cloud_login_command(*, no_browser: bool = False) -> int:
    """Complete the public-client OAuth flow and enable cloud sync."""
    from snodo.config import ConfigManager
    from snodo.infrastructure.cloud_oauth_client import CloudOAuthClient, CloudOAuthError, oauth_state, pkce_verifier
    from snodo.infrastructure.cloud_oauth_loopback import CloudOAuthLoopbackError, receive_authorization_code
    from snodo.infrastructure.cloud_oauth_store import CloudOAuthState, load_oauth_state, save_oauth_state

    try:
        mgr = ConfigManager()
        config = mgr.load()
        cloud = config.get("cloud", {}) if isinstance(config, dict) else {}
        previous = load_oauth_state()
        client = CloudOAuthClient(config)
        client.discover()
        client_name = client.client_name
        client_id = previous.client_id
        if client_id is None or previous.registered_client_name != client_name:
            client_id = client.register_client()
        verifier = pkce_verifier()
        state = oauth_state()
        def auth_url(redirect_uri: str) -> str:
            return client.authorization_url(client_id, redirect_uri, verifier, state)

        code, actual_redirect_uri = receive_authorization_code(
            auth_url, state,
            open_browser=(lambda _url: False) if no_browser else None,
        )
        tokens = client.exchange_code(client_id, code, actual_redirect_uri, verifier)
        access_token = tokens.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise CloudOAuthError("OAuth token response did not include an access token")
        expires_in = tokens.get("expires_in")
        expires_at = time.time() + expires_in if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool) else None
        save_oauth_state(CloudOAuthState(
            client_id=client_id,
            access_token=access_token,
            refresh_token=tokens.get("refresh_token") if isinstance(tokens.get("refresh_token"), str) else None,
            expires_at=expires_at,
            scope=tokens.get("scope") if isinstance(tokens.get("scope"), str) else None,
            registered_client_name=client_name,
        ))
        mgr.set_value(("cloud", "sync_enabled"), True)
        account = _oauth_account_label(access_token)
        message = f"✓ Signed in to snodo cloud{f' as {account}' if account else ''}; audit sync enabled."
        print(message)
        if cloud.get("api_key"):
            print("OAuth is now in use. Run `snodo cloud connect <api_key>` to switch back to API-key authentication.")
        return 0
    except CloudOAuthLoopbackError as err:
        text = str(err).lower()
        if "timed out" in text:
            print("Cloud sign-in timed out. Run `snodo cloud login` and complete the browser prompt.", file=sys.stderr)
        elif "authorization server returned an error" in text:
            print("Cloud sign-in was denied. Run `snodo cloud login` to try again.", file=sys.stderr)
        else:
            print("Cloud sign-in failed. Run `snodo cloud login` to try again.", file=sys.stderr)
        return 1
    except Exception as err:
        # OAuth helpers deliberately expose only credential-free errors.
        reason = str(err).lower()
        if "network" in reason or "unreachable" in reason:
            print("Cloud sign-in server is unreachable. Check your connection and try again.", file=sys.stderr)
        else:
            print("Cloud sign-in failed. Check your connection and try `snodo cloud login` again.", file=sys.stderr)
        return 1


def _oauth_account_label(access_token: str) -> str:
    """Extract a non-secret account/org label from JWT claims without verification."""
    try:
        import jwt
        claims = jwt.decode(access_token, options={"verify_signature": False, "verify_exp": False})
        for key in ("organization_name", "org_name", "organization", "org", "email", "name"):
            value = claims.get(key)
            if isinstance(value, str) and value:
                return value
    except Exception:
        return ""
    return ""


def cloud_disconnect_command() -> int:
    """Clear cloud API key and OAuth login, and disable sync."""
    from snodo.config import ConfigManager
    from snodo.infrastructure.cloud_oauth_store import clear_oauth_state

    mgr = ConfigManager()
    mgr.set_value(("cloud", "api_key"), "")
    mgr.set_value(("cloud", "sync_enabled"), False)
    clear_oauth_state()

    print("Disconnected from snodo cloud.")
    return 0


def cloud_logout_command() -> int:
    """Best-effort revoke the refresh token, then always clear local OAuth tokens."""
    from snodo.config import ConfigManager
    from snodo.infrastructure.cloud_oauth_client import CloudOAuthClient
    from snodo.infrastructure.cloud_oauth_store import clear_oauth_state, load_oauth_state

    state = load_oauth_state()
    revoked = False
    revoke_attempted = False
    if state.has_refresh_token() and state.client_id:
        revoke_attempted = True
        try:
            config = ConfigManager().load()
            client = CloudOAuthClient(config)
            metadata = client.discover()
            if metadata.revocation_endpoint:
                client.revoke(state.client_id, state.refresh_token)
                revoked = True
        except Exception:
            print("Cloud token revocation failed; local sign-out is complete.")
            clear_oauth_state()
            return 0
    clear_oauth_state()
    print("Signed out of snodo cloud.")
    if revoke_attempted and not revoked:
        print("Cloud token revocation was unavailable or failed; local sign-out is complete.")
    return 0


def cloud_status_command() -> int:
    """Show cloud connection and sync state."""
    from snodo.config import ConfigManager, get_cloud_ingest_url
    from snodo.infrastructure.cloud_sync import CloudSyncState

    mgr = ConfigManager()
    config = mgr.load()
    cloud = config.get("cloud", {}) if isinstance(config, dict) else {}

    api_key = cloud.get("api_key", "")
    sync_enabled = cloud.get("sync_enabled", False)
    api_url = get_cloud_ingest_url(config)
    from snodo.infrastructure.cloud_oauth_store import load_oauth_state
    oauth = load_oauth_state()
    has_oauth = bool(oauth.access_token or oauth.refresh_token)
    has_api_key = isinstance(api_key, str) and bool(api_key.strip())
    auth_method = "OAuth login" if has_oauth else "API key" if has_api_key else "none"

    print(f"Snodo cloud: {'connected' if auth_method != 'none' else 'not connected'}")
    print(f"  Authentication: {auth_method}")
    if has_oauth:
        print(f"  Access token expires: {_format_ts(oauth.expires_at) if oauth.expires_at else 'unknown'}")
        print(f"  Refresh token present: {'yes' if oauth.has_refresh_token() else 'no'}")
        account = _oauth_account_label(oauth.access_token) if oauth.access_token else ""
        print(f"  Account/organisation: {account or 'unknown'}")
    if has_oauth and has_api_key:
        print("  OAuth login takes priority over the API key.")
    print(f"  API URL:    {api_url}")
    print(f"  Sync:       {'enabled' if sync_enabled else 'disabled'}")
    if auth_method == "none":
        print("  Run: snodo cloud connect <api_key> or snodo cloud login")

    state = CloudSyncState()
    summary = state.get_summary()
    if summary:
        print()
        print("Sync status per session and project:")
        for sid, info in sorted(summary.items()):
            if not sid:
                print(
                    "  legacy global refusal entry: ignored (refusals are per-session); "
                    "remove the empty-key entry from ~/.snodo/cloud_sync.json if desired."
                )
                continue
            seq = info.get("last_synced_sequence", 0)
            at = info.get("last_synced_at", 0)
            ts = _format_ts(at) if at else "never"
            pending = info.get("pending_count", 0)
            last_attempt = info.get("last_attempt_at", 0)
            last_attempt_ts = _format_ts(last_attempt) if last_attempt else "never"
            last_error = info.get("last_error")
            display_id = f"project (sessionless) [{sid.removeprefix('project:')}]" if sid.startswith("project:") else sid
            if state.is_refused(sid):
                reason = info.get("refused_reason", "refused by server")
                rng = info.get("refused_range")
                range_str = f"seq {rng[0]}-{rng[1]}" if rng else "unknown range"
                print(f"  {display_id}:  BLOCKED (refused: {reason}, {range_str})  last_seq={seq}  synced_at={ts}")
                print(f"    pending={pending}  clear with `snodo cloud sync --all --force`;")
                print("    fix the refused request or retry explicitly to resume.")
            elif info.get("refused"):
                reason = info.get("refused_reason", "previous response")
                status = info.get("refused_status_code", "unknown status")
                print(
                    f"  {display_id}:  RECHECK (old non-terminal refusal HTTP {status}: {reason}); "
                    f"sync will retry; pending={pending}"
                )
            else:
                print(f"  {display_id}:  last_seq={seq}  synced_at={ts}")
                print(f"    pending={pending}  last_attempt={last_attempt_ts}")
            if last_error:
                print(f"    last_error: {last_error}")
            liveness_at = info.get("last_liveness_push_at")
            print(f"    last_liveness_push: {_format_ts(liveness_at) if liveness_at else 'never'}")
            if info.get("last_liveness_error"):
                print(f"    last_liveness_error: {info['last_liveness_error']}")
            if info.get("liveness_failure_count"):
                print(f"    liveness_failures: {info['liveness_failure_count']}")
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


def cloud_sync_command(sync_all: bool = False, session_id: str = "", force: bool = False) -> int:
    """Sync audit events to snodo cloud for one or more sessions.

    --all: sync all sessions and sessionless audit events for the current project
    --session <id>: sync a specific session
    --force / --retry: force re-attempt sync for refused sessions
    (no flags): sync the current active session
    """
    from snodo.config import ConfigManager, get_cloud_ingest_url, get_cloud_lease_url
    from snodo.infrastructure.paths import require_project_root
    from snodo.infrastructure.cloud_sync import CloudSyncDispatcher
    from snodo.infrastructure.audit import AuditLog, AuditError

    mgr = ConfigManager()
    config = mgr.load()
    cloud = config.get("cloud", {}) if isinstance(config, dict) else {}

    api_key = cloud.get("api_key", "")
    api_url = get_cloud_ingest_url(config)
    lease_url = get_cloud_lease_url(config)

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
            # An audited-but-missing session id is evidence the audit log (a
            # property of the project) and the session store (a property of the
            # snodo home) have diverged — e.g. the session was created under a
            # different SNODO_HOME. Say so, or the operator cannot sync a
            # session the audit chain says ran.
            audited = session_mgr.is_audited_but_missing(
                session_id, project_root,
            )
            if audited:
                print(
                    f"Error: Session {session_id} is cited by the audit log but "
                    f"has no file under {session_mgr.sessions_dir}.",
                    file=sys.stderr,
                )
                print(
                    "  It was likely created under a different SNODO_HOME, so "
                    "its events cannot be synced from this store.",
                    file=sys.stderr,
                )
                return 1
            print(f"Error: Session not found: {session_id}", file=sys.stderr)
            return 1
        sessions_to_sync = [session]

    elif sync_all:
        sessions_to_sync = session_mgr.list_sessions(project_root=project_root)

    else:
        # Active session for current mode
        state = read_state(project_root)
        mode = state.current_mode
        session = None
        if mode:
            session = session_mgr.get_active_session(mode, project_root)
        if session is not None:
            sessions_to_sync = [session]

    dispatcher = CloudSyncDispatcher()
    total_synced = 0
    total_failed = 0
    total_partial = 0

    for session in sessions_to_sync:
        sid = session.session_id
        proot = session.project_root

        audit_path = str(Path(proot) / ".snodo" / "audit.log")
        try:
            audit_log = AuditLog(audit_path)
        except AuditError as err:
            print(f"  {sid}  ✗ corrupt audit log: {err}")
            total_failed += 1
            continue

        result = dispatcher.sync(
            sid, proot, audit_log, api_key, api_url,
            force=force, lease_url=lease_url,
        )

        if result.get("refused"):
            reason = result.get("reason", "refused by server")
            pending = result.get("pending", 0)
            if result.get("synced", 0):
                print(
                    f"  {sid}  PARTIAL: {result['synced']} events synced; refusal: {reason}; "
                    f"{pending} event(s) pending. Retry with `snodo cloud sync --session {sid} --force`."
                )
                total_synced += result["synced"]
                total_partial += 1
            else:
                print(
                    f"  {sid}  BLOCKED (refused: {reason}); {pending} event(s) pending. "
                    f"Clear with `snodo cloud sync --session {sid} --force`."
                )
            total_failed += 1
        elif result["synced"] > 0:
            print(f"  {sid}  ✓ {result['synced']} events synced")
            total_synced += result["synced"]
        elif result.get("failed"):
            reason = result.get("reason")
            suffix = f": {reason}" if reason else ""
            print(f"  {sid}  ✗ sync failed{suffix}; {result.get('pending', 0)} event(s) pending. Retry with `snodo cloud sync --session {sid}`.")
            total_failed += 1
        else:
            print(f"  {sid}  — no new events")

    # Sessionless events only have a local project cursor; ``project:<id>`` is
    # not a valid server session_id. Never send that synthetic key over wire.
    if not sessions_to_sync and not session_id:
        from snodo.infrastructure.audit import AuditLog, AuditError

        project_audit_path = str(Path(project_root) / ".snodo" / "audit.log")
        try:
            project_audit = AuditLog(project_audit_path)
        except AuditError as err:
            print(f"  project (sessionless)  ✗ corrupt audit log: {err}")
            total_failed += 1
        else:
            pending = len(project_audit.events)
            if pending:
                print(f"  project (sessionless)  — skipped {pending} event(s): cloud requires a session id")

    if total_synced > 0 or total_failed > 0:
        print()
        scope = f"{len(sessions_to_sync)} session(s)" if sessions_to_sync else "the project"
        print(f"Synced {total_synced} events across {scope}.")
        if total_failed:
            print(f"  {total_failed} session(s) had failures; partial progress: {total_partial}.")

    return 0 if total_failed == 0 else 1
