# Spec: Keep the MCP event loop responsive under blocking work

## Problem

FastMCP's stdio transport runs sync tool handlers inline on a single event loop
(confirmed: func_metadata.py:95-96 calls fn(**args) directly for sync handlers, no
thread pool). Any handler that blocks freezes the whole server — every other tool call
queues behind it and hits the client's ~4-minute timeout. This is why list_jobs (a
millisecond state read) timed out: it was stuck behind a blocked handler.

After the bounded-read fix, the only remaining blocking operation is
ShellMCP.run_tests() -> subprocess.run(timeout=300) (shell.py:119-125), up to
5 minutes. It is reached via two paths:
- _handle_validate_task (server.py:651-716)
- the run_tests tool through _dispatch_tool (server.py:615-643)

get_job_logs is NOT a concern anymore — it's bounded/fast. Do not touch it.

## Fix

### 1. Offload the blocking subprocess off the event loop

Make the handlers that invoke shell.run_tests() run that subprocess off the loop
(async handler awaiting the blocking call in a worker thread — FastMCP natively awaits
async tool fns, base.py:67). While the tests run in a thread, the event loop stays
free to serve other tool calls (list_jobs, get_job_status, etc.).

Scope: only the handlers that call run_tests. Do not convert the fast handlers
(get_job_status, list_jobs, dispatch_task, resolve_disagreement, get_job_logs) —
they stay inline.

### 2. Protect the only mutable shared state: _validation_token

Offloading makes concurrent handler execution possible, so the one high-risk shared
attribute must be guarded. _validation_token is written by _handle_validate_task
(server.py:699) and _handle_dispatch_task (server.py:752-753), and read by
_enforce_wf1 (server.py:599,610) on every mutating tool. Concurrency creates a
TOCTOU race (token checked as absent while being set in a thread, or consumed between
check and use).

Guard the token's read-check-write with a lock that is safe across the worker-thread
boundary (the token may be written inside the offloaded thread and read on the loop) —
i.e. a threading.Lock, not an asyncio-only lock. Wrap the check in _enforce_wf1 and
the set/consume in validate_task/dispatch_task under the same lock.

### 3. Lock AuditLog.get_history() reads

Writes are already lock-protected (audit.py:67-88). get_history() (audit.py:99-101)
reads self.events without the lock. Acquire the existing self._lock around that read
so concurrent append + read can't observe a torn list or diverge from disk.

## Constraints

- Do NOT offload get_job_logs (already bounded) or any fast state-read handler.
- Do NOT change run_tests' own behaviour — still subprocess.run with its timeout;
  only move where it runs (off the loop).
- Immutable-after-init state (protocol, project_root, mode_id, _tools, _mcp_map) and
  stateless services (token_issuer, backing MCPs) need no locks.
- The transport/call_tool dispatch must correctly await async handlers while still
  calling sync handlers directly (mixed sync/async dispatch).

## Acceptance

- While validate_task / run_tests runs a long (e.g. mocked 30s) subprocess, a
  concurrent list_jobs / get_job_status call returns immediately — server not wedged.
- Concurrent validate_task (token writer) + a mutating tool (_enforce_wf1 reader)
  cannot observe an inconsistent token state.
- Audit hash chain stays intact under concurrent append + get_history.

## Tests

- Offload: patch run_tests to sleep N seconds; assert a concurrent list_jobs call
  completes well before the sleep finishes (proves the loop isn't blocked).
- Token lock: concurrent validate + enforce_wf1 access doesn't raise a spurious WF1
  violation and doesn't double-consume.
- get_history under concurrent append returns a consistent snapshot.
- Existing server + jobs test suites still pass.
