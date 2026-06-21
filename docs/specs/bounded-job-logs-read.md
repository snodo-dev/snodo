# Spec: Bounded tail read for `get_logs`

## Problem

`JobManager.get_logs()` (`jobs/__init__.py:217-239`) calls `log_file.read_text()` —
reads the entire log file into memory, then `splitlines()` and slices the last N.
For a verbose LLM coder session, the log is megabytes. On the single-threaded FastMCP
stdio event loop, that read+split is a CPU-bound block that freezes the whole server
for its duration — every subsequent tool call (even millisecond state reads like
`list_jobs`) queues behind it and times out.

## Fix

Make `get_logs` a bounded tail read: when `tail` is set, read only the last chunk of
the file from the end, never the whole thing. O(tail), not O(file size).

## Changes

### `jobs/__init__.py:217-239` — `get_logs`

When `tail` is a positive int:
- Seek from the end of the file and read only the trailing bytes needed to cover
  `tail` lines (read a bounded window, e.g. last 64KB, expand only if it doesn't yet
  contain `tail` newlines, capped at a hard maximum like 1MB).
- Decode that window, split, return the last `tail` lines.
- Never call `read_text()` on the full file when `tail` is set.

When `tail` is None or <= 0:
- Cap the read at a hard maximum (e.g. last 1MB) rather than unbounded `read_text()`.
  An unbounded full-file read must not be possible from this path.

Behaviour to preserve:
- File doesn't exist -> return "" (already handled, keep it).
- Reading a file a live process is still writing -> return whatever is on disk now,
  immediately. Never wait for the writer or for EOF.
- Partial first line when the window cuts mid-line is acceptable (drop it or keep it,
  but do not scan backwards unboundedly to find a clean line start).

## Constraints

- No changes to the MCP handler `_handle_get_job_logs` (`mcp/server.py:798-827`) — the
  fix is entirely in `get_logs`.
- No change to the return contract (still returns log text as a string).
- This does NOT fix the systemic single-loop blocking (validate_task / run_tests) —
  that's a separate spec. This only bounds the log read.

## Acceptance

- `get_logs(job_id, tail=50)` on a 10MB log returns in milliseconds, reading only a
  small trailing window — not the whole file.
- `get_logs` on a live (still-writing) job returns immediately with current contents.
- Missing log file -> "".
- Tail correctness: returns the last N lines (allowing one possibly-truncated leading line).

## Tests

- Large file (~5MB of lines): `tail=50` returns 50 lines and does not read the full
  file (assert via timing or by patching read to detect full reads).
- Small file: tail returns all lines when fewer than N exist.
- Missing file: returns "".
- `tail=None`: returns at most the 1MB cap, not unbounded.
