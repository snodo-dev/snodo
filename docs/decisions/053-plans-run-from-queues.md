# ADR 053 — Plans run from queues; a queue stops at its first unfinished plan

## Status

Accepted.

## Context

Long unattended runs work today only because the orchestrator is told, in
prose, to loop: author a small plan, run it, check it, park it if blocked,
author the next, and keep a self-ping timer so it notices if it has stopped.
Two different jobs are mixed in that loop. Progression — start the next
plan when the current one finishes, stop when something is stuck — is
mechanical and needs no judgement. Authoring, unblocking and reordering
need judgement. Because both live in the orchestrator, a stalled or sleeping
orchestrator stops the mechanical part too, and every operator has to
re-teach the loop in their own words.

## Decision

snodo owns progression through **queues**; the orchestrator keeps the
judgement.

1. **A queue is a named, ordered list of plan names.** A plan belongs to at
   most one queue. A queue named `default` always exists and cannot be
   removed. `snodo queue create <name>` adds one; `snodo queue` lists them
   with their plans in order.
2. **A plan joins the back of `default` when it passes `validate_plan`**
   (CLI or MCP), unless it is already queued. Joining at creation was
   rejected: the runner would pick up a plan whose specs are still being
   written. A plan leaves its queue when every task in it is `completed`.
   On first use, existing incomplete plans that pass validation are added
   to `default` in the order they were created.
3. **A queue is FIFO with head-of-line blocking.** The runner runs the front
   plan through the existing plan-run path (completed tasks are skipped,
   clean passes auto-merge per ADR 018). When the plan completes, it is
   removed and the next one starts. When the plan ends with any task
   `blocked`, `errored` or `unmerged`, the queue stops there; the runner
   never skips ahead. Plans later in a queue may build on earlier ones, so
   a queue is also the dependency model: independent work goes in another
   queue.
4. **`snodo queue run`** runs `default`; `run x` runs queue `x`; `run x,y`
   runs `x` and `y` in parallel, one runner each; `run --all` runs every
   queue sequentially in creation order, `default` first, moving to the
   next queue when one empties or stops. A runner holds a per-queue lock,
   and a second runner on the same queue is refused. A runner exits when
   nothing in its queues is runnable; it does not wait. Resuming a stopped
   queue is a new `snodo queue run` (a nudge, from the orchestrator or the
   operator). Scheduling runs is a separate concern, not decided here.
5. **`--non-blocking` relaxes the order; it is opt-in.** With it, a plan
   that ends `blocked`, `errored` or `unmerged` stays where it is in the
   queue and the runner carries on with the next. Only a non-blocking run
   can run plans in parallel: `--parallel-run N` runs up to N plans of one
   queue at a time, and without `--non-blocking` it has no effect beyond a
   printed note: the run stays one plan at a time. Defaults come from the
   protocol (`queue.non_blocking`, default false; `queue.parallel_runs`,
   default 1, read only when non-blocking), so the default behaviour is the
   strict one above. In a non-blocking run, plans in the same queue must
   not depend on each other; `snodo queue validate` flags the order
   problems it can see.
6. **Order is explicit and movable.** `snodo queue move <plan>` with
   `--front`, `--before <plan>`, `--after <plan>` and/or `--to <queue>`
   (back of that queue unless a position is given). Moving a plan that is
   running is refused. A corrective plan is placed with `--front` so it
   runs before the plan it unblocks.
7. **`snodo queue validate [x]` reports; it changes nothing.** For each
   queue: whether the front plan is runnable or where it stopped and why;
   whether every queued plan still passes plan verification against the
   current tree; order problems, from each plan's planned paths — a plan
   citing a path that a *later* plan in the same queue creates, or a path
   only a plan in *another* queue creates; plans in different queues that
   touch the same files (warning); and whether a runner is active.
8. **Every command is an MCP tool** (`queue_list`, `queue_create`,
   `queue_move`, `queue_validate`, `queue_run`), governed by the mode grant
   like every other tool (ADR 047). `queue_run` is asynchronous and returns
   a job id, like `run_plan`. The MCP guide gains a `queues` topic teaching
   the orchestrator's loop: validate, reorder, unblock, run.
9. **No new state.** Queue name and position are the only new data, kept in
   one file under `.snodo/`. "Stopped" is derived from the front plan's
   existing task statuses; there is no queued, parked or stopped plan
   status, no new severity and no new halt type. Refusals (moving a running
   plan, a second runner) are command errors, not states.

## Consequences

The orchestrator's unattended loop reduces to: validate the queues, reorder
or move what is in the wrong place, unblock the front of any stopped queue
(fix the spec and rerun, or add a corrective plan with `--front`), and run.
Progression no longer depends on the orchestrator being awake between
plans. Parallel queues can still collide at merge; that surfaces as
`unmerged` and stops only that queue, so related work belongs in one queue.
`run_plan` on a single plan keeps working; a runner will not start a plan
that is already running. What the cloud sees of queue runs is decided in ADR 054.

## Alternatives

Skipping a blocked plan by default was rejected: later plans may depend on
it, and making that safe needs explicit dependency links between plans —
the ordering problem queues exist to avoid. It stays available as the
explicit `--non-blocking` choice. Priority numbers were rejected
in favour of explicit positions. `queued`/`parked` plan statuses were
rejected: both are derivable, and the status vocabulary is closed. Running
the queue inside `snodo serve` was rejected in favour of an explicit
`snodo queue run`, so the operator decides when work starts.
