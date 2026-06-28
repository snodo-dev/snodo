# ADR 013 — Kleene-closure auto-fix recovery loop

## Status
Accepted

## Context
The paper specifies a Kleene closure (`*`) over the execution loop: a phase
sequence may repeat zero-or-more times with a termination guarantee. The
engine implements the bounded *inner* loop (governance → validate → execute →
post_validate → move_next, capped at 50 iterations) and the *data model* for a
task tree (Task.parent_task_ref / Task.depth; planner.py maintains depth =
parent.depth + 1). What is missing is the closure itself: today a post-execute
validation warning or blocker is terminal — `_post_validate_node` sets
is_blocked and routes straight to the `blocked` terminal node. `spawned_subtasks`
is declared on LoopState but never produced or consumed.

## Decision
When post-execute validation returns a RECOVERABLE result (warning, or an
overridable blocker), the engine does not halt. It spawns an auto-fix subtask
and re-enters the loop, repeating until the post-execute validators pass
(fixpoint) or a bound is reached.

Semantics:
- Trigger: only post_execute validator outcomes. Pre-execute failures and
  hard, non-overridable blockers (INV3) halt immediately — never auto-fixed.
- Recovery unit: a spawned subtask, not an in-graph cycle. The subtask is a
  first-class Task with depth = parent.depth + 1, parent_task_ref = parent.id,
  and a spec seeded from the failed validators' justifications + the failure
  context already emitted by _auto_write_failure_context.
- Closure: driven by a new outer `run_to_closure` driver (engine), not a
  LangGraph cycle. Fixpoint = a task completes with clean post-validation and
  zero newly spawned subtasks.
- Termination (well-founded; all configurable in protocol):
    max_recovery_depth      default 3   (cap on Task.depth for fix subtasks)
    max_total_fix_attempts  default 10  (global cap across the closure tree)
    per-node iterations      existing 50 inner-loop cap
  On exhaustion the engine falls back to the existing escalate/block terminal
  path with halt_type "recovery_exhausted".
- Which severities trigger recovery is configurable (default: blocker only;
  opt-in to also auto-fix warnings). Respects ADR 002 (warn withholds approval)
  and ADR 006 (severity cap).

## Invariants preserved
- INV1: each spawned subtask runs the full governance gate and mints its own
  validation token; no token reuse across subtasks.
- INV3: non-overridable blockers halt; recovery cannot override them.
- INV4: the entire closure tree is audited. Each spawn emits a
  `subtask_spawned` event (parent_ref, depth, triggering validators); each
  fixpoint/exhaustion emits a `recovery_resolved` / `recovery_exhausted` event.
- INV5: subtasks are session-scoped under the same (mode, project) as the
  parent; no new persisted tokens.

## Alternatives considered
- In-graph LangGraph cycle (edge auto_fix → execute): rejected. Reuses one Task
  identity for many fix attempts, muddying the audit chain and making the
  closure tree unobservable for experiments.
- Treat every post-validate failure as terminal (status quo): rejected — it is
  the gap this ADR closes.

## Consequences
- New engine driver `run_to_closure`; CLI `_execute_task` calls it instead of a
  single graph.invoke.
- New audit event types; existing log readers must tolerate them.
- Live experiments can measure closure depth, fix-attempt counts, and
  resolution vs escalation rates directly from the audit log.
