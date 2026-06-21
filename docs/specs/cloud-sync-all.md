# snodo cloud sync --all: retroactive audit log sync

## Intent
snodo cloud sync --all walks all sessions for the current project and
ships any unsynced audit events to snodo-cloud. Uses the same
CloudSyncDispatcher as the automatic post-task sync — same batching,
same cursor, same retry logic. Fills the gap for sessions that existed
before snodo cloud connect was run.

## What to change

### cli/commands/cloud_cmd.py
Add sync subcommand:

snodo cloud sync [--all] [--session <session_id>]
  --all: sync all sessions for the current project
  --session: sync a specific session by ID
  (no flags): sync the current active session only

For each session to sync:
  - Load the session (SessionManager.get_active_session or load by ID)
  - Get the audit log path (.snodo/audit.log for that session's project)
  - Call CloudSyncDispatcher.sync(session_id, project_root, audit_log,
      api_key, api_url)
  - Print progress: "Syncing sess_xxx... ✓ 42 events / ✗ failed"

Requires cloud.api_key to be set (sync_enabled check). If not:
  "Run snodo cloud connect first."

### cli/main.py
Register sync under the cloud sub-app.

## Acceptance criteria
- snodo cloud sync --all syncs all sessions for current project
- snodo cloud sync --session <id> syncs one session
- snodo cloud sync syncs the active session
- Progress printed per session
- No snodo account → clear error
- Uses existing CloudSyncDispatcher — no new sync logic
- Cursor advances correctly — only confirmed events marked synced

## Testing
- Unit: --all iterates all sessions
- Unit: --session syncs specific session
- Unit: no api_key → clear error
- Unit: uses CloudSyncDispatcher (not new logic)
- Full suite passes

## Constraints
- Read cli/commands/cloud_cmd.py, infrastructure/cloud_sync.py,
  infrastructure/session.py (list_sessions) before touching anything
- No new sync logic — reuse CloudSyncDispatcher entirely
- Touch: cli/commands/cloud_cmd.py, cli/main.py only
