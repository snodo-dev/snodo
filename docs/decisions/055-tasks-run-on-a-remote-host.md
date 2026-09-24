# ADR 055 — Tasks can run on a remote host; the local machine stays the single writer

## Status

Accepted.

## Context

Plan queues (ADR 053) made parallel work cheap to start: several queues, each
running waves of tasks, across several projects. Every task runs its coder,
its validators and its test commands on the operator's machine, and
`coder.concurrency` is only enforced inside one plan run. Three workstreams
dispatching queues at once exhausted a 16 GB laptop and crashed it. The
operator has stronger machines reachable over SSH (e.g. `ssh GPU2` works
with no prompt). Docker's `DOCKER_HOST` is the model: the CLI stays local,
the work runs where the daemon is.

## Decision

1. **Selecting a host.** `execution.host` in config names an SSH host; the
   `SNODO_HOST` environment variable overrides it; neither set means run
   locally, exactly as today. The host value is whatever `ssh <host>`
   accepts; snodo adds no SSH configuration of its own.
2. **What runs remotely: the whole task loop.** Pre-execute validation, the
   coder, verification/test commands and post-execute validation run on the
   host. Plans, queues, the audit log, task status, liveness, cloud sync and
   the merge stay on the local machine.
3. **Code moves with git, not mounts.** The host keeps its own clone of the
   project at `execution.host_path` (default: the same path relative to the
   remote home). Before a task, snodo pushes the base commit to that clone
   over SSH; the task's worktree is created there. After the task, snodo
   fetches the task branch back and merges locally as today. Network
   filesystems are rejected: heavy small-file I/O and file locks (git index,
   token store, queue and wave locks) do not survive them.
4. **The host runs snodo.** The host has snodo installed. Before any task
   snodo runs a preflight over SSH: the host is reachable non-interactively,
   the remote snodo version equals the local one, and the remote clone path
   exists and is a clone of the same project. Any failure refuses the task
   with a message naming the check; it never falls back to local silently.
5. **Credentials travel in memory.** The local machine resolves provider
   keys as it does today (including `@keys/` decryption) and sends them to
   the remote worker as the first line of its stdin; the worker keeps them
   in memory only and never writes them to disk. Tools with their own login
   store on the host (e.g. `opencode auth login`) are configured on the host
   once.
6. **The host is a stateless worker; the local machine is the only writer.**
   The worker writes JSON lines to stdout, one object per line, each with
   `"v": 1` and a `"kind"`:
   - `log`: `{"stream": "stdout|stderr|snodo", "line": "..."}`
   - `audit`: `{"event_type": "...", "data": {...}}` — the worker never
     writes an audit log; the local machine appends these to its own log,
     so the hash chain stays local and whole.
   - `status`: `{"task_ref": "...", "status": "..."}` using existing task
     status values only.
   - `heartbeat`: `{}` at least every 5 seconds.
   - `final`: `{"outcome": "<existing task status>", "branch": "...",
     "head_sha": "..."}` — exactly one, last.
   The local reader writes the job log (so `snodo logs --watch` works
   unchanged), appends audit events, updates task status and pushes
   liveness from this stream.
7. **Failure is an existing status.** If the stream ends without `final`,
   the SSH process exits non-zero, or no line arrives for 60 seconds, the
   task ends `errored`. The host keeps the worktree so a retry can use it.
   No new task, plan or job status, severity or halt type is introduced.
8. **One host first.** Several hosts with a capacity each (`local: 1,
   GPU2: 4`) is a later extension and is not decided here.

## Consequences

The laptop only carries the orchestrator and snodo's bookkeeping; the heavy
work moves to the host. Everything that reads local state — `snodo logs`,
`snodo plan status`, queues, liveness, cloud sync — keeps working without
knowing where a task ran. The host must have snodo at the same version and
its own clone, which the preflight checks rather than assumes.

## Alternatives

Mounting the local worktree on the host (or `~/.snodo`) was rejected for
the I/O and locking reasons in point 3, and because it would expose private
key material on a network share. Running the whole project on the host and
driving `snodo serve` over SSH works today without code and remains valid,
but loses the local view of plans, queues and history.
