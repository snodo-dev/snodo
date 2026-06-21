# snodo cloud: connect command + audit log sync

## Intent
Two things in one ticket:
1. snodo cloud connect <api_key> — stores the snodo-cloud API key in
   ~/.snodo/config.yml so all cloud features (tunnel, sync) read from
   one place.
2. Automatic audit log sync to api.snodo.dev/ingest after each task —
   opt-in (only when cloud_api_key is configured), async, non-blocking,
   with cursor-based batching and proper retry/backoff.

## Contract (from snodo-cloud ADR)
POST https://api-staging.snodo.dev/ingest  (staging)
POST https://api.snodo.dev/ingest          (production)
Auth: Authorization: Bearer <api_key>
Body: {
  session_id: "sess_...",
  project_path: "/absolute/path/to/project",
  events: [  // 1-50 events per batch
    {
      sequence: int,
      timestamp: "ISO8601",
      event_type: str,
      data: {...},         // opaque — full audit event data
      previous_hash: str | null,
      event_hash: str      // sha256 of event content
    }
  ]
}
Response: 200 → advance cursor
          429 → backoff by retry_after header
          5xx → exponential backoff, up to 5 retries

## What to build

### 1. Config schema (cli/config.py)
Add cloud section to the default config and ConfigManager:
  cloud:
    api_key: ""          # set by snodo cloud connect
    api_url: "https://api.snodo.dev"  # override for staging
    sync_enabled: false  # opt-in — only sync when explicitly enabled

### 2. snodo cloud connect (cli/commands/cloud_cmd.py, new)
snodo cloud connect <api_key>
  - Validate key format (starts with sndo_staging_ or sndo_live_)
  - Store in ~/.snodo/config.yml: cloud.api_key = <key>
  - Set cloud.sync_enabled = true
  - Print: "✓ Connected to snodo cloud. Audit sync enabled."

snodo cloud disconnect
  - Clear cloud.api_key and set sync_enabled = false
  - Print: "Disconnected from snodo cloud."

snodo cloud status
  - Show: connected/disconnected, api_key prefix (first 16 chars + ...),
    sync_enabled, last_synced_sequence per session

Register under main.py as `snodo cloud <subcommand>`.

### 3. Audit sync cursor (infrastructure/cloud_sync.py, new)
CloudSyncState — tracks per-session sync progress:
  - Storage: ~/.snodo/cloud_sync.json
    {session_id: {last_synced_sequence: int, last_synced_at: float}}
  - get_cursor(session_id) → last_synced_sequence (0 if never synced)
  - advance_cursor(session_id, sequence)
  - Uses file-based storage (same pattern as agents.json) with atomic
    write (tmp + rename)

### 4. Sync dispatcher (infrastructure/cloud_sync.py)
CloudSyncDispatcher:
  sync(session_id, project_root, audit_log, api_key, api_url)
    - Read audit events from audit_log since last cursor
    - Batch into groups of up to 50
    - For each batch:
        POST to api_url/ingest with auth header
        200 → advance cursor
        429 → sleep retry_after seconds, retry
        5xx → exponential backoff (1s, 2s, 4s, 8s, 16s), up to 5 retries
        Other error → log warning, stop (do not advance cursor)
    - Never raises — all errors are logged, never block the caller
    - Returns: {synced: int, failed: bool}

  Uses httpx (already a dep), async not required — this runs in a
  background thread (same pattern as recon fan-out).

### 5. Wire into run_cmd.py
After task completion (in the finally block where checkpoint is saved),
if cloud.sync_enabled and cloud.api_key is set:
  - Spawn a background thread: CloudSyncDispatcher.sync(...)
  - Fire and forget — never block task completion
  - The engineer sees no output from sync unless it fails (log warning)

## Acceptance criteria
- snodo cloud connect stores key, enables sync
- snodo cloud disconnect clears key, disables sync
- snodo cloud status shows connection state
- After task completion, new audit events are shipped in batches of ≤50
- Cursor advances only on 200 response
- 429 respects retry_after
- 5xx retries up to 5 times with exponential backoff
- Network failure never blocks or crashes the task
- sync_enabled: false (default) → no HTTP calls ever made
- Key format validated (sndo_staging_ or sndo_live_ prefix)
- Atomic cursor write (tmp + rename, same as agents.json)

## Testing
- Unit: snodo cloud connect stores key and enables sync
- Unit: key format validation (valid/invalid prefixes)
- Unit: snodo cloud disconnect clears key
- Unit: cursor get/advance/atomic write
- Unit: batch size ≤50 respected
- Unit: 429 → retry_after sleep
- Unit: 5xx → exponential backoff, max 5 retries
- Unit: network error → never raises, returns failed=True
- Unit: sync_enabled=false → no HTTP calls
- Unit: cursor advances only on 200, not on error
- Full suite passes

## Constraints
- Read cli/config.py (config schema + ConfigManager), cli/commands/
  (existing command pattern), infrastructure/audit.py (AuditLog —
  how events are read), cli/commands/run_cmd.py (the finally block
  where sync hooks in) before touching anything
- httpx already a dep — use it, no new network deps
- Never block task completion — background thread only
- Never raise out of the sync path — all errors logged as warnings
- Opt-in only: default config has sync_enabled: false
- Key never logged in full — only first 16 chars in status/logs
- Touch: cli/config.py, new cli/commands/cloud_cmd.py, new
  infrastructure/cloud_sync.py, cli/commands/run_cmd.py (hook only)
