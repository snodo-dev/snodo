#!/usr/bin/env bash
#
# The gate host's half of `make gate` / `make gate-ci`.
#
# Why this is a file and not one long ssh command: a remote command that
# outlives the client that started it is the fault this exists to remove. The
# Makefile runs the gate over a terminal (`ssh -tt`), so the command here is
# the leader of a session whose controlling terminal is the connection itself.
# An interrupted gate, a dropped link or a killed `make` hangs that session up,
# and the trap below takes the whole process group — pytest and every xdist
# worker — down with the leader. Inline in the Makefile there is nowhere to
# hold a trap, and an interrupted gate left the remote tree running with no
# parent watching it until the box stopped accepting a login shell.
#
# The gate's own output must stay what it was without a terminal. A pty makes
# pytest colour its output and draw progress; running the checks through
# `| cat` hands every tool a pipe instead, so they emit the plain lines they
# always did. The pty still needs its output post-processing: `ssh -tt` copies
# the OPERATOR's raw termios onto this end, which leaves `opost` off, and with
# `opost` off `onlcr` is inert, so a bare LF moves down a row without returning
# to column zero and every line of a run started where the previous one ended —
# the staircase. `stty opost onlcr` restores the CR that makes a line a row, and
# its failure is not swallowed because the output's readability depends on it.
# `pipefail` is what keeps a failing check's non-zero exit status from being
# replaced by `cat`'s, which is always zero.

set -o pipefail

# Run the checks for one target, in the order the target has always run them.
# `gate` and `gate-ci` differ only in the pytest invocation — that divergence
# is the one CONTRIBUTING documents, and nothing else here may drift.
gate_run() {
  local target="$1"
  local -a pytest_args
  case "$target" in
    gate)
      pytest_args=(tests/ -q -n "$GATE_JOBS")
      ;;
    gate-ci)
      pytest_args=(tests/ -m "" -n "$GATE_JOBS" --tb=short --timeout=60 \
        --cov --cov-report=term-missing --cov-fail-under=75)
      ;;
    *)
      echo "gate_remote.sh: unknown target: $target" >&2
      return 2
      ;;
  esac
  uv sync --all-extras -q \
    && uv run pytest "${pytest_args[@]}" \
    && uv run ruff check . \
    && uv run lint-imports \
    && uv run python scripts/enforce_file_length.py \
    && uv run python scripts/enforce_docs_coverage.py \
    && uv run python scripts/enforce_vocabularies.py \
    && uv run python scripts/enforce_changelog.py \
    && uv run python scripts/enforce_cloud_schema.py
}

# Take one of GATE_SLOTS flock slots for this host, or wait for one. The lock
# is held on an open file descriptor, so it vanishes with this process when an
# interrupted gate dies — there is no stale slot to reap. Where flock is absent
# the gate runs unbounded rather than refusing; the lifetime trap still holds.
gate_acquire_slot() {
  command -v flock >/dev/null 2>&1 || return 0
  local slots="${GATE_SLOTS:-2}" slot_dir="${GATE_ROOT:-$HOME/Dev/gates}/.gate-slots"
  mkdir -p "$slot_dir"
  while :; do
    local slot=0
    while [ "$slot" -lt "$slots" ]; do
      exec 9>"$slot_dir/slot$slot"
      if flock -n 9; then return 0; fi
      exec 9>&-
      slot=$((slot + 1))
    done
    sleep 2
  done
}

# Print every descendant of *parent*, deepest first.
gate_descendants() {
  local parent="$1" child
  for child in $(pgrep -P "$parent" 2>/dev/null); do
    gate_descendants "$child"
    printf '%s\n' "$child"
  done
}

# End the gate's whole tree.
#
# `kill -TERM 0` reaches this shell's process group, which is how a hangup is
# meant to travel — but a child that calls setsid is in a new group too, so a
# group signal walks past it. This does not matter for pytest's own workers
# (execnet spawns them in this group), but it does for the suite's jobs, which
# `snodo.jobs.runner.spawn_background` starts with start_new_session=True, and
# an interrupted gate has to take the whole tree or the orphan problem just
# moves one level down. Walking the descendant tree catches those; the group
# signal is sent as well because it is O(1) and reaches same-group children
# the walk might race as they fork.
#
# TERM first, then KILL, so a process that traps or ignores TERM still goes.
# The reap must survive its own group signals to finish that escalation, so
# they are ignored for the shell running it; the final group KILL ends the
# shell too, which is the point — the client is already gone.
gate_reap() {
  local root=$$ pids
  trap '' TERM INT HUP
  pids=$(gate_descendants "$root" | tr '\n' ' ')
  kill -TERM $pids 2>/dev/null
  kill -TERM -"$root" 2>/dev/null
  sleep 1
  pids=$(gate_descendants "$root" | tr '\n' ' ')
  kill -KILL $pids 2>/dev/null
  kill -KILL -"$root" 2>/dev/null
}

# Run a command as the thing whose hangup ends the gate.
#
#   gate_supervise CMD [ARG...]
#
# It is a function, not an inline command, so the suite can exercise the
# mechanism against a probe command on any host, the gate host included,
# without a second copy of the trap drifting from the one the gate uses.
gate_supervise() {
  # Reset the trap before reaping so a reap cannot re-enter it, then end the
  # tree. 129 is the conventional 128+HUP for an interrupted command.
  trap 'trap - HUP INT TERM; gate_reap; exit 129' HUP INT TERM
  gate_acquire_slot || return $?
  # Every tool must see a pipe rather than the terminal, or its output
  # changes -- that is what the `| cat` below is for. The connection's own
  # output post-processing must be back on: `ssh -tt` copies the operator's
  # raw termios here, leaving `opost` off, and while `opost` is off `onlcr`
  # does nothing, so a bare LF moves down a row without returning to column
  # zero and every line starts where the last one ended. Only a terminal
  # needs this -- with no tty the output is a pipe and cannot staircase --
  # and where there is one, a failed `stty` is left visible rather than
  # swallowed, because the output's readability depends on it.
  if [ -t 0 ]; then
    stty opost onlcr
  fi
  # Background the pipeline and `wait` for it: bash defers a trap until the
  # foreground command returns, and a hung-up pty never returns one, so a
  # command run in the foreground would leave the trap holding an open hangup
  # it never gets to run. `wait` is interruptible, so the trap fires.
  # `cat` keeps the checks' bytes; pipefail carries their status out of it.
  # stdin is /dev/null: the gate is not interactive, and a check that read the
  # terminal would block on the pty the hangup mechanism needs.
  "$@" </dev/null 2>&1 | cat &
  local gate_pid=$!
  wait "$gate_pid"
}

gate_main() {
  local target="${1:?usage: gate_remote.sh <gate|gate-ci>}"
  : "${GATE_HOME:?GATE_HOME must be set}"
  : "${GATE_ROOT:=$HOME/Dev/gates}"
  : "${GATE_JOBS:=24}"
  : "${GATE_SLOTS:=2}"
  export PATH="$GATE_HOME/.local/bin:$PATH"
  gate_supervise gate_run "$target"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  gate_main "$@"
fi
