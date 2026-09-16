# Spec: a gate run ends on the gate host when it ends on the operator's

## Root cause
`make gate` / `make gate-ci` run, roughly:

```
ssh $(GATE_HOST) '… uv run pytest tests/ -q -n $(GATE_JOBS) …'
```

`ssh` with no terminal allocation leaves the remote command in a session with no
controlling terminal, and nothing ties its process tree to the connection. When the
operator interrupts the gate, drops the link, or kills `make`, the SSH session ends — but
the remote `pytest` and its `-n 24` workers keep running with no parent watching them.

The gate host then saturates. Several abandoned runs plus several live ones oversubscribe
it until forking a login shell takes tens of seconds. `sshd` still accepts connections, so
the box answers ping and a bare TCP probe while looking unreachable over SSH. Killing the
orphaned remote processes restored it immediately in the observed case.

`GATE_JOBS` defaults to 24, and `GATE_NAME` gives each worktree its own gate directory so
several gates can run at once by design — but nothing caps how many do.

## Fix

Three changes, none of which touches what a gate checks or the order it checks it in:

1. **Allocate a terminal.** The Makefile runs the gate as `ssh -q -tt $(GATE_HOST) …`.
   The remote command is now a session leader whose controlling terminal *is* the
   connection. An interrupt, a dropped link or a killed `make` hangs that session up, and
   SIGHUP reaches the remote process group.

2. **Trap the hangup and reap the tree.** `scripts/gate_remote.sh` (new) holds the trap,
   which the Makefile's one-line recipe had nowhere to put. A hangup (or INT/TERM) reaps
   the gate's whole descendant tree, not just its process group: the suite's jobs are
   started with `start_new_session=True` (`snodo.jobs.runner.spawn_background`), so a child
   that calls `setsid` is in a new group and a bare group signal walks past it.

3. **Keep the gate's output unchanged.** A pty would otherwise colour pytest's output and
   terminate lines with CRLF. The wrapper hands every tool a pipe (`… | cat`) and stops the
   pty rewriting newlines (`stty -onlcr`), and `pipefail` keeps a failing check's exit status
   from being replaced by `cat`'s. A clean gate prints byte-for-byte what it always did.

4. **Bound concurrency, do not forbid it.** `GATE_SLOTS` (default 2) gates share a host at
   once through `flock` slots under `GATE_ROOT`. Waiting for a slot is silent, so a clean
   gate on an idle host prints nothing new. The lock is held on an open file descriptor and
   vanishes with the process, so an interrupted gate leaves no stale slot to reap.

## Alternatives considered
- **An explicit bound on the run** (`timeout`): cuts a slow gate short rather than a dead
  one. It does not end a gate when the operator stops caring about it, which is the
  property the ticket asks for.
- **A trap without a terminal:** a remote command that never sees SIGHUP still had to be
  signalled from somewhere, and with no client watching there is no one to send it.
- **Refusing to start when a gate is already running against the host:** forbids the
  concurrency that is a feature. Bounding is the smaller change and keeps several
  worktrees gating at once working.

## Scope
`Makefile` (gate targets and variables), `scripts/gate_remote.sh` (new),
`tests/scripts/test_gate_remote.py` (new), `CHANGELOG.md`, this spec. `GATE_HOST`,
`GATE_HOME`, `GATE_ROOT`, `GATE_DIR` and `GATE_NAME` semantics are unchanged; per-worktree
gate directories stay.

## Tests
- a normal gate's exit status is unchanged (a failing check still fails the gate)
- a normal gate's stdout is byte-for-byte unchanged through the wrapper
- an interrupted gate (HUP/INT/TERM) leaves no descendant running
- an interrupted real `pytest -n` run leaves neither its test child nor a pytest process
- a child that daemonises (owns its own session) is reaped too
- canary: a supervisor with the trap removed leaves its child behind, so the lifetime test
  can still fail
- the Makefile still asks for `ssh -tt`, the wrapper, and `GATE_SLOTS`

## Verify
`uv run pytest tests/ -q -n auto -m "" && uv run ruff check . && uv run lint-imports`

## Touch
`Makefile`, `scripts/gate_remote.sh`, `tests/scripts/test_gate_remote.py`,
`CHANGELOG.md`, this spec.
