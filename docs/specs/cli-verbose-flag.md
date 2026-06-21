# Add --verbose / -v global flag to snodo CLI

## Intent
Engineers need visibility into what snodo is doing — HTTP calls,
sync progress, audit events, model resolution, etc. A global --verbose
flag sets the logging level to DEBUG so all internal logging is visible.
Works with any command: snodo --verbose cloud sync --all,
snodo --verbose run "task", snodo --verbose serve.

## What to change

### cli/main.py
Add a global --verbose / -v flag to the Typer app callback:

@app.callback()
def main_callback(
    verbose: bool = typer.Option(False, "--verbose", "-v",
        help="Enable verbose output (debug logging)")
):
    if verbose:
        import logging
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(name)s %(levelname)s %(message)s"
        )

This runs before any subcommand, so all subsequent logging calls
(including CloudSyncDispatcher, model discovery, audit, etc.) emit
at DEBUG level.

### cloud_sync.py — ensure it uses logging
The dispatcher should use Python logging (not print) for all
informational output. If it uses print() for the "HTTP 403" messages,
switch to logger.warning() / logger.debug() so --verbose controls
visibility. The per-session progress lines (✓ N events synced) stay
as print() — those are user-facing output, not debug logs.

## Acceptance criteria
- snodo --verbose cloud sync --all shows HTTP request details,
  response codes, cursor advances, retry attempts
- snodo --verbose run "task" shows engine debug output
- snodo --verbose serve shows FastMCP startup details
- Without --verbose: same clean output as before, no change
- --verbose / -v both work

## Testing
- Unit: --verbose sets logging level to DEBUG
- Unit: -v alias works
- Full suite passes (verbose flag must not affect non-verbose tests)

## Constraints
- Read cli/main.py (app callback pattern), infrastructure/cloud_sync.py
  (current logging vs print usage) before touching
- Global flag only — no per-command --verbose
- Do not change user-facing print() output — only internal logging
- Touch: cli/main.py, infrastructure/cloud_sync.py (logging only)
