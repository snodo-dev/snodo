# Spec: Wire `dispatch_task` to `JobManager`

## Problem

`dispatch_task` (`mcp/server.py:667-700`) is a stub. It stores the task spec in
`self._pending_dispatch` — an in-memory variable at `server.py:388` that is never
read anywhere. Consumes the validation token and returns `{"status": "accepted"}`,
but no execution is triggered. Every MCP dispatch is silently dropped.

Side effect: MCP-driven sessions are invisible to the dashboard — SessionManager
is never touched, so no session is created.

## Fix

Wire to `JobManager.submit()` (`jobs/__init__.py:125-171`), which already persists
a job and spawns a background `snodo run` process.

## Changes

1. `mcp/server.py:667-700` _handle_dispatch_task: replace `self._pending_dispatch =
   task_spec` with `JobManager.submit(task_spec, mode=<active_mode>)`. Return the
   job ID: `{"status": "accepted", "task_id": job_id, "task_spec": task_spec}`.
   Token consumption stays — correct ordering, consumed on acceptance.

2. `mcp/server.py:388`: remove `_pending_dispatch` — dead code.

3. `ProtocolMCPServer.__init__`: instantiate `JobManager` from `project_root`
   (already available). Add as instance attribute. No new constructor parameter —
   call sites unchanged.

4. Active mode: `dispatch_task` must pass mode to `JobManager.submit()`. The server
   knows the mode it was started with (from protocol config). Confirm where stored
   on the server instance and use it; if not stored, add during `__init__`.

## Constraints

- Do not duplicate JobManager logic — it already handles persistence and spawning.
- Job ID returned must match what appears in SessionManager so orchestrator can
  correlate dispatch → session.
- No changes to engine loop, validators, token system, or JobManager internals.
- snodo serve call sites unchanged.

## Acceptance

- `dispatch_task` returns `{"status": "accepted", "task_id": "<id>", "task_spec": "..."}`
- Background `snodo run` starts and executes the task
- Session created in SessionManager → visible in dashboard
- External orchestrator has task_id to track execution
- `_pending_dispatch` gone from codebase
- Tests: mock JobManager injection, verify job_id returned, token consumed,
  submit called with correct spec + mode
