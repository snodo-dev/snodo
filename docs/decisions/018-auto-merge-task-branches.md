# ADR 018 — Auto-merge task branches on successful completion

## Status
Accepted

## Context
Every task runs in its own git worktree on a branch
`task/<id>/<slug>`, created off the base branch. The base branch is hardcoded
as `main` in two places: worktree creation (`infrastructure/worktree.py`) and
`GitMCP.merge_branch` (`tools/git.py`). Nothing ever merges the task branch
back: `engine/loop.py:_move_next_node` sets `is_complete = True` and nothing
else, and `merge_branch` has no engine caller.

The consequence is silent data loss-by-omission: a task that completes cleanly
leaves its work on the task branch, and the next task branches off `main` and
cannot see it. This was observed twice — a fix task reimplemented the feature
it was asked to repair because the branch holding that feature had never
landed.

## Decision
On genuine completion (closure outcome `resolved`), merge the task branch into
the resolved base branch. This is opt-in, not a new default:

- **Protocol level**: `execution.auto_merge` (default `false`) — existing
  protocols are unaffected.
- **Mode override**: `mode.auto_merge` (default `null`) — a mode may opt a phase
  in or out, so phases whose output a human should read before it lands can
  keep the manual flow. `Protocol.auto_merge_enabled(mode_id)` resolves the
  mode override over the protocol default.

The base branch is no longer assumed to be `main`. `resolve_base_branch()`
resolves it from the repository — the remote default (`origin/HEAD`), falling
back to `main` — and is the single source of truth used by both worktree
creation and `merge_branch`.

A merge conflict is an **escalate**, not a blocker and not a crash: the merge
is aborted (base branch stays clean), the task branch and worktree survive, and
the run reports an escalation for a human to resolve. Cleanup is asymmetric —
the worktree and branch are removed only after a successful merge; otherwise
both are left intact.

Merging happens in the CLI layer (`run_cmd.py:_execute_task`), not the engine:
the engine runs *inside* the worktree (whose MCPs are rooted there), so it must
not perform the merge; the CLI owns the worktree lifecycle and the main
checkout.

## Invariants preserved
- Merge only on `closure.outcome == "resolved"` — a blocked, escalated, or
  recovery-exhausted task never merges, so incomplete work can never land.
- No merge when isolation was degraded (worktree was never created) — the work
  is already in the working tree and there is nothing to merge.
- The engine and MCP paths agree on "complete": the merge is gated strictly on
  the closure driver's `resolved` outcome, the same signal that every other
  completion consumer uses. The MCP `merge_branch` tool remains a distinct,
  token-gated manual action (reviewer mode), not an auto-merge.

## Alternatives considered
- Merge inside the engine `_move_next_node`/`_complete_node`: rejected — the
  engine runs in the worktree and cannot checkout the base branch (checked out
  in the main worktree).
- Merge by default: rejected — automatically writing to the base branch is a
  trust decision; it must be opt-in per protocol/mode.

## Consequences
- New `ExecutionConfig.auto_merge` and `Mode.auto_merge` fields; golden
  snapshots updated to include them.
- New audit events `task_merged`, `merge_conflict_escalated`,
  `merge_failed_escalated`.
- A conflicting auto-merge leaves a task branch and worktree behind for manual
  resolution — `snodo task list`/`snodo task abandon` already expose these.
