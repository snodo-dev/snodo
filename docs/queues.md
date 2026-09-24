<!-- snodo-guide topic="queues" summary="Validate, unblock, and nudge queues through their ordered plans" section="# Keep queues moving" -->
# Keep queues moving

Snodo advances plans through queues; the orchestrator keeps the judgement.
Use `queue_validate` to see whether each queue can proceed, whether its plans
still validate, and whether the visible plan order or cross-queue file
collisions need attention. Validation is a report: it does not edit queues.

## The orchestration loop

1. Call `queue_validate` before a run. Reorder or move plans that are in the
   wrong place with `queue_move`; related work belongs in one queue, in the
   order it depends on. Keep independent plans in separate queues.
2. If a queue is stopped, start with its front plan. If its spec is wrong,
   correct the spec and run `queue_validate` again. If the work needs a
   separate correction, author a corrective plan and use `queue_move` to put
   it at the front of the stopped queue.
3. Call `queue_run` to resume queue progression. The runner exits when there
   is nothing runnable; it does not wait for an orchestrator or a later fix.
   After unblocking a stopped queue, nudge it with `queue_run` again.
4. Inspect the run's job and plan outcomes, then repeat the validation and
   ordering pass before deciding what should run next.

Plans join the back of `default` when they pass `validate_plan`, unless they
are already queued. Keep plans that depend on each other in the same queue so
the default FIFO, stop-at-the-front behavior preserves their order. Use
separate queues for independent work.

## Relaxing queue order

Use `--non-blocking` only when it is acceptable to leave a blocked, errored,
or unmerged plan in place and continue with later plans. Later plans in that
queue must be independent of it. `--parallel-run N` runs up to N plans from
one queue concurrently, and is meaningful only with `--non-blocking`; use it
only for independent plans. The strict blocking, one-plan-at-a-time behavior
is the default.
