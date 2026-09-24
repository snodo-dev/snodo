<!-- snodo-guide topic="queue-triage" summary="Adopt inherited plans into runnable queues" section="# Adopt an inherited queue" -->
# Adopt an inherited queue

When queues are first used in an existing project, every incomplete plan that
passes validation joins `default` in creation order (ADR 053). That order may
contain stale work or a blocked front plan. Triage the inherited plans before
starting a runner; do not treat their presence in the queue as proof that the
work is still wanted.

## Inspect before changing anything

1. Call `queue_list` and record every queue and its plan order. For each
   inherited plan, call `get_plan` and inspect its task records and outcomes.
   Use the plan's own evidence to decide whether its intent is still wanted,
   whether its work was completed another way, and whether a failed outcome
   can be corrected. Do not infer intent from age or queue status alone.
2. Give every plan one disposition: **keep** it as valid work; **fix** its
   plan/spec or prerequisites because the work is still wanted; or **retire**
   it because the work is no longer wanted or its result is already present
   another way. Resolve uncertain intent with the human rather than guessing.
3. Retire a queued plan with `queue_remove`. This removes only its queue entry;
   its plan and task records stay on disk, and it can rejoin by passing
   `validate_plan` again. Keep a note of the plan and evidence for the review.
   For a kept or fixed plan, make the needed plan/spec correction and call
   `validate_plan` to check it; validation may put an unqueued plan at the back
   of `default`.

## Shape queues, then validate

Keep dependent plans together in one queue, ordered so every prerequisite comes
first. Put independent work in separate queues, creating named queues with
`queue_create` as needed. Use `queue_move` to place the kept plans in the
intended queues and dependency order. Do not use a non-blocking run to hide a
dependency or skip a plan that needs a decision.

Call `queue_validate` after triage and ordering, and resolve reported plan
verification failures, order issues, and cross-queue path warnings before the
first `queue_run`. Validation reports; it does not change queue state. Then
start the intended queue or queues with `queue_run`; follow its returned job
with `get_job_status` and `get_job_logs`. Inspect plan and task outcomes with
`get_plan`. A queue stops at its first blocked, errored, or unmerged plan; fix
or review that plan and validate the queues again before nudging with another
`queue_run`.

## Example: eleven inherited Droptrack plans

Suppose `queue_list` shows eleven plans in `default`: three pending, four
blocked, and four unmerged. The labels below stand for actual plan names; the
disposition comes from reading each plan's records, not from the status alone.

| Plans | Records and intent | Decision |
|---|---|---|
| `schema`, `api` (pending) | Still wanted; `api` depends on `schema` | Keep together in this order |
| `auth`, `billing` (blocked) | Still wanted; records identify a missing prerequisite now available | Fix the plans/specs, validate, and keep before their dependants |
| `reporting`, `export` (blocked) | Records show the requested outcome was delivered by the current reporting flow | Retire with `queue_remove` |
| `search`, `alerts` (unmerged) | Still wanted; work is independent from the schema/API chain | Keep in separate queues; inspect and validate their unmerged work before retrying |
| `legacy-import`, `old-theme` (unmerged) | Their records show the feature was superseded and the desired result already shipped elsewhere | Retire with `queue_remove` |
| `metrics` (pending) | Still wanted, but independent of the other work | Keep in its own queue |

This leaves seven plans: `default` holds the dependency chain `auth → schema →
billing → api` (the records show `auth` and `billing` are prerequisites for
`schema` and `api` respectively); `search`, `alerts`, and `metrics` each have
their own queue. Run `queue_validate` for all queues and resolve its findings
before starting any queue. If the two unmerged plans cannot be made cleanly
mergeable, leave them waiting and report the conflict rather than claiming
they merged. If a known divergence is deliberately accepted, record it for
human review with the specific impact.

## Morning review for the human

After runs finish, leave a concise review backed by `get_plan` and the job
tools. Include:

- **Merged:** plan name and each task that completed/merged; include the job
  outcome where useful.
- **Waiting:** plan and task, current status, and the reason (for example a
  blocked prerequisite, validation failure, or unmerged conflict). Say what
  decision or correction is needed next.
- **Retired:** each removed plan and the record showing it was obsolete, no
  longer wanted, or delivered another way. Confirm that its plan records
  remain on disk.
- **Human look:** any work accepted with a known divergence, conflict, or
  unmerged result, with the specific risk and decision needed.

Use `get_job_status` to establish job completion and `get_job_logs` for run
details; use `get_plan` for task-level status. Report only tasks recorded as
completed/merged as merged. Keep waiting work and known divergence visible so
the human can decide what should happen next.
