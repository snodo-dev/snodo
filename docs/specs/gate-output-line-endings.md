# Spec: a gate's output lands in rows, not a staircase

## Root cause

#304 gave a gate its process lifetime by running it over a terminal (`ssh -tt`).
That works — an interrupted gate no longer orphans pytest on the host — but every
line of a gate's output now begins where the previous one ended, so a full run
prints its pytest progress as a diagonal that wraps and overwrites until it is
unreadable.

The first attempt set `stty onlcr` in `gate_supervise`, on the theory that the CR
should come from the remote pty. It is on main and the staircase persists. The
reason is the layer `ssh -tt` uses:

- `ssh` puts the operator's terminal into raw mode for the duration of the
  connection, which clears `opost` (output post-processing).
- `sshd` copies the *client's* termios onto the remote pty, so the pty the
  remote command gets also comes up with `-opost`.
- With `opost` off, `onlcr` is inert: newline translation is one of the output
  post-processing steps `opost` gates. `onlcr` alone does not turn `opost` back
  on.

So the remote pty emits bare LFs, a bare LF moves down a row without returning
to column zero, and the staircase follows. The old call was both a no-op and
silent about it: `stty onlcr 2>/dev/null || true` hid whatever it did.

Established against the gate host: with a raw client terminal the remote pty
reports `-opost -onlcr` under `ssh -tt`; the old wrapper emits bare `LF`; adding
`opost` to the call emits `CRLF`.

## Fix

In `gate_supervise`, turn output post-processing back on as well as newline
translation, and let a failure be visible:

```sh
if [ -t 0 ]; then
  stty opost onlcr
fi
```

- `opost onlcr` makes each LF a CRLF at the pty, which is the layer that can
  supply the CR once the client's raw mode has removed it.
- The `-t 0` guard means the call is made only where a terminal exists: with a
  pipe there is no tty to configure and no staircase to fix.
- No `2>/dev/null || true`, because the output's readability depends on the
  setting; a failure to apply it should be visible rather than swallowed.
- The `| cat` sink stays, so the checks still see a pipe and emit the plain
  lines they always did. `pipefail` still carries a failing check's status out
  of `cat`.

The lifetime mechanism is untouched: the session still runs over the pty and the
trap still reaps the whole tree on a hangup, interrupt or kill.

## Alternatives considered

- **Keep `stty onlcr`, drop the `2>/dev/null || true`:** would make the failure
  visible, but the call is still inert while `opost` is off, so the staircase
  would remain and the gate would only report that it could not fix it. The
  missing piece is `opost`.
- **Drop `ssh -tt` and get lifetime another way:** the pty is what delivers the
  hangup to a process whose parent is gone. An explicit bound (`timeout`) cuts a
  slow gate short rather than a dead one, and signal forwarding from a local
  watcher reintroduces the fault #304 removed in a new place. Keeping `-tt` and
  fixing the newline translation is the smaller change.
- **Rewrite newlines in the wrapper (`| sed 's/$/\r/'`):** would change the
  bytes the operator sees and put translation in the wrong layer; the pty's
  `opost` is the intended place.

## Scope

`scripts/gate_remote.sh`, `tests/scripts/test_gate_remote.py`, `CHANGELOG.md`,
this spec. What a gate checks, the order it checks it in, its exit status, the
per-worktree gate directory and the lifetime behaviour are unchanged.

## Tests

- a gate's output on a raw-mode pty has CR before LF on every line (each line
  begins at column zero);
- the setting the output depends on is not hidden behind `2>/dev/null || true`;
- the existing lifetime tests still hold: an interrupted gate (HUP/INT/TERM)
  leaves no descendant, an interrupted real `pytest -n` run leaves neither its
  test child nor a pytest process, and a daemonised child is reaped too.

## Verify

`uv run pytest tests/ -q -n auto -m "" && uv run ruff check . && uv run lint-imports`

## Touch

`scripts/gate_remote.sh`, `tests/scripts/test_gate_remote.py`, `CHANGELOG.md`,
this spec.
