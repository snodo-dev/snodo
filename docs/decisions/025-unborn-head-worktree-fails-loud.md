# ADR 025 — Unborn-HEAD worktree creation fails loud, never degrades to no isolation

## Status

Accepted

## Context

Every task in snodo runs inside a fresh git worktree
(`.snodo-worktrees/task_<id>/`) branched off the resolved base branch, so the
agent cannot write to the operator's real working tree. On a repository with
no commits the base branch is **unborn** and does not resolve:

```
fatal: invalid reference: main
WARNING: Worktree creation failed — running WITHOUT isolation.
WARNING: No task branch will be created. Files change current working tree.
```

`create_worktree` propagated the git failure up to `setup_for_task`, which
swallowed it (`except Exception → return None`). The CLI path interpreted
`None` as "warning, continue", and the background-job path printed
`Worktree: skipped (...)`. Both then executed the task **in the operator's
working tree** — a safety property lost by default, on the state every
greenfield repository starts in. The runbook (docs/runbooks/01) reproduced it
and recommended making an initial commit first; snodo kept running anyway.

## Decision

1. **A structural isolation failure raises, it never degrades.**
   `create_worktree` detects an unborn HEAD (no resolvable `HEAD` commit)
   before issuing any git command and raises `WorktreeIsolationError` with
   actionable guidance (make an initial commit; or pass `--no-isolation`).
   `setup_for_task` no longer swallows worktree-creation errors and returns
   `None` — the exception propagates to the caller.

2. **The CLI refuses to run without isolation unless the operator says so.**
   `snodo run` aborts (exit 1) with the guidance when no worktree could be
   established and `--no-isolation` was not passed. The flag exists exactly so
   the degradation is an explicit human decision, never an implicit default.
   The run also records a `worktree_isolation_failed` audit event.

3. **A background job is refused up front.** `JobManager.submit` has no
   operator at a console to read a warning, so when the worktree cannot be
   created the job is marked `failed` and `submit()` raises — it is never
   spawned un-isolated in the project tree.

4. **`--no-isolation` is explicit only.** It is a new CLI flag
   (`snodo run --no-isolation`); nothing sets it automatically.

## Alternatives considered

- **Create the initial commit automatically.** Rejected: snodo's consent
  boundary is `snodo init` (ADR 014); committing on the operator's behalf
  outside that boundary is a write they did not request.
- **Keep the warning but require `--no-isolation` on failure.** Rejected as
  the primary behaviour: a warning that degrades a safety property is the
  failure mode this ADR is closing. Aborting is the honest default.

## Consequences

- `snodo run` on a fresh repository (no commits) now stops with guidance
  instead of running the agent against the user's real working tree.
- `--no-isolation` lets an operator deliberately accept a degraded run.
- Background dispatch on a repository with no commits fails the job
  immediately and visibly rather than silently running un-isolated.
- `WorktreeIsolationError` is a distinct, catchable type; transient git
  failures still propagate as ordinary exceptions (the CLI fails loud on any
  worktree-creation failure, not just unborn HEAD).
- Test suites that exercise job submission without a real git repo now mock
  `create_worktree`.
