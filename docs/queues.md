<!-- snodo-guide topic="queues" summary="Validate, unblock, and nudge queues through their ordered plans" section="# Keep queues moving" -->
# Keep queues moving

Snodo moves plans forward; the orchestrator supplies judgement. Snodo's queue
runner processes a queue FIFO and stops at its first plan that ends `blocked`,
`errored`, or `unmerged`. The orchestrator writes plans, unblocks stopped work,
reorders plans, and nudges queues by starting another run. A runner exits when
there is nothing runnable; it does not wait for a future fix or for more plans.

## Read validation and decide

Call `queue_validate` before starting and whenever a runner exits or a queue
changes. It is a read-only report; take the indicated action before starting
more work:

- **Front runnable:** `runnable` is true. If `runner_active` is false, start
  `queue_run`; if a runner is active, follow its existing job instead of
  starting a duplicate.
- **Front stopped:** `front.stopped_by` gives the task, status, and reason.
  Read the plan and job evidence, then use the matching recovery recipe below.
  Do not skip the front in strict mode.
- **Plan no longer verifies:** `verified` is false and
  `verification_errors` explains why. Repair the plan/spec with the plan tools,
  confirm it validates, and validate the queue again before running it.
- **Order problem within a queue:** `order_problems` can report
  `later_plan_creates_cited_path`. The earlier plan needs something a later
  plan creates. Put dependent plans in dependency order with `queue_move`, or
  rewrite the plan/spec if the dependency is not real.
- **Path only another queue creates:**
  `other_queue_creates_cited_path` means this plan cites a path produced only
  in a different queue. Keep the dependency in one queue and order its plans
  producer first; do not rely on two queues finishing in a particular order.
- **Cross-queue file overlap:** `cross_queue_warnings` names queues that cite
  the same path. Treat it as a coordination risk: combine related work in one
  ordered queue, or ensure genuinely independent changes will not conflict
  before running those queues together.
- **Runner active:** `runner_active` means that queue already has a runner.
  Keep polling the `job_id` returned by that `queue_run`; do not start another
  run for that queue. A lock report is not a substitute for checking the job's
  terminal result.

## Keep the unattended loop alive

1. Use `queue_list` and `queue_validate` to see the order, readiness, and any
   active runner. Write and validate plans with the plan tools. Use `queue_create`
   for a separate lane and `queue_move` to put dependencies in order.
2. Start runnable queues with `queue_run`. It returns a `job_id`; remember the
   job and queues it covers.
3. Poll `get_job_status` on a short backoff: wait a few seconds for the first
   check, then about 10, 20, 40, and up to 60 seconds between checks while it
   remains active. Inspect `get_job_logs` for errors or unclear outcomes and
   `get_plan` for per-task outcomes. Reset to a short wait after a meaningful
   status change. Do not busy-poll.
4. When the runner job is terminal, validate the queues again. A finished job
   may have stopped at a blocked, errored, or unmerged front; act on the report
   and evidence. When the front is runnable and no runner is active, call
   `queue_run` again. Repeat for the whole unattended session.
5. Independently set a self-check for about every 10 minutes. On each tick,
   confirm that this orchestration loop is still alive, that its next poll or
   action is scheduled, and that no completed runner was left without
   validation. This check is separate from job polling: a live job does not
   prove the orchestrator itself will wake up to handle its exit.

There is no need to keep a runner alive after it exits. Revalidation and a new
`queue_run` are the nudge after adding a plan, fixing a stopped front, or seeing
that the runner has drained its current work. Plans that pass `validate_plan`
join the back of `default` unless already queued.

## Unblock the front plan

- **Blocked:** Read `get_plan`, the task halt/reason, and relevant job logs.
  Correct a wrong spec with the plan tools, or use the evidence to write a
  focused corrective plan when the implementation is wrong. Validate the plan
  and queue, then call `queue_run` again. Never bypass a validator blocker or
  blindly repeat unchanged work.
- **Errored:** Inspect `get_job_status` and `get_job_logs` to distinguish a
  transient, repaired execution problem from a bad spec or persistent engine,
  provider, or environment fault. After fixing the cause, retry by calling
  `queue_run` again; otherwise stop and leave the fault for the human. Do not
  spin on repeated errors.
- **Unmerged:** Inspect the plan and job logs to understand the conflict and
  preserve the unmerged work. Resolve the conflict against the merged result,
  or write a corrective plan against that result, then validate and call
  `queue_run` again. Do not claim it landed or rerun unchanged work just to
  merge it.

Write a **corrective plan and move it to the front** when a focused, separately
judged change must happen before the stopped plan can safely continue, or when
the existing plan's completed/recorded work should be preserved rather than
rewritten. Use `queue_move` to place that correction before the plan it
unblocks. Repair the original plan in place only when its spec/structure is
wrong and changing it will not erase or misrepresent prior evidence.

## Choose safe lanes

Keep dependent plans in one queue, producer before consumer. Run independent
work in separate queues, which can be started together with one `queue_run`
for several queues. Cross-queue runners proceed independently; do not assume
one queue completes before another. Check `queue_validate` warnings and avoid
concurrent file overlap that could turn into an unmerged result.

`--non-blocking` leaves a blocked, errored, or unmerged plan in place and lets
later plans in that queue run. Use it only when every later plan is independent
of every plan it might pass: these must be independent plans, and leaving the
stopped change behind must be acceptable. `--parallel-run N` is safe only
together with `--non-blocking`, and only when up to N plans in that same queue
are mutually independent with no ordering or file conflict. These options
relax FIFO; they are unsafe for
producer/consumer work, shared files, or any queue where later work assumes the
front plan succeeded. Strict FIFO, one plan at a time, is the default.

## Stop and leave a human a useful note

Stop nudging a queue when progress requires human judgement or access: an
`escalate` decision, credentials or environment repair, an unresolved merge
conflict, repeated unexplained errors, or a dependency/overlap you cannot make
safe. Also stop when the work is complete or no queue is runnable and there is
no justified next plan. Do not poll or retry forever. Leave a concise note with
the queue and plan, job IDs, last status/reason and relevant logs, what landed
or remains unmerged, what you tried, and the precise human decision or repair
needed. Independent, safe queues may continue while one is parked.

## Example: one overnight session

At 22:00, validate `default` and `docs`: `default` contains an API plan then a
dependent client plan, while `docs` has independent work. Keep the dependent
pair in `default`, check overlap warnings, and start both lanes in one
`queue_run`. Poll each returned job on the short backoff and keep the separate
10-minute self-check scheduled. At 23:10, the API plan is `blocked`; read its
halt and logs, prepare a focused corrective plan, and `queue_move` it to the
front of `default`. The docs runner has exited cleanly, so validate `docs` and
start it again if it has more runnable plans. Re-run `queue_run` for the
corrected `default` lane; when it exits, validate and nudge it again so the
client plan can proceed after its dependency. If the correction instead needs
human authorization or a conflict cannot be safely resolved, leave `default`
parked with the note above, continue only safe independent work, and report
what the morning operator must decide.
