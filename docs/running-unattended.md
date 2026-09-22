# Running Snodo unattended

This guide is for an orchestrator that takes a stream of intents and keeps
turning them into judged, recorded work without a person watching every tool
call. Snodo's plan tools author work; the execution loop and validators decide
whether it passes. Treat each run as a job with an outcome to inspect, not as a
single call that completes the intent.

## The loop: intent to landed work

For each intent:

1. Call `propose_plan` to put the intent into a plan. Read the returned wave and
   task structure; split independent work into separate tasks and express their
   dependencies in waves.
2. Call `generate_spec` for each task. Make each spec independently actionable
   and state observable acceptance criteria.
3. Call `validate_plan` while authoring to catch plan-shape problems. It is a
   useful check, not an authorization token or a prerequisite: `run_plan`
   checks the plan again before it starts anything.
4. Call `run_plan` without `wait=true`. It starts a background job and returns
   `job_id` immediately. That response means the job was accepted, not that any
   task passed or that the intent is complete.
5. Follow the `job_id` with `get_job_status` until the job is terminal. Use
   `get_job_logs` when the result needs context, and `get_plan` for per-task
   status and validation detail. Read the terminal result and task outcomes
   before choosing the next intent.
6. Record what happened in a durable operator-readable report (or the
   orchestrator's run log): the intent and plan/job ids, what ran, what landed,
   what is parked, why, and any follow-up needed. Do not rely on a later human
   reconstructing this from a conversation.

Plan and job vocabularies are different. Job status is `queued`, `running`,
`completed`, `failed`, or `unmerged`. Per-task plan status is `pending`,
`in_progress`, `completed`, `blocked`, `errored`, or `unmerged`. A finished job
is not necessarily a successful plan: inspect its `exit_code`, logs, and task
statuses. `get_plan` can be read while a run is in progress as well as after.

## Poll at a useful cadence

A plan run is asynchronous; waves commonly take minutes. Poll soon after start
to catch quick plan/validation or startup failures, then back off while coders
are working. For example, wait a few seconds before the first poll, then use
increasing waits such as 10, 20, 40, and 60 seconds (capped at about a minute),
resetting to a short delay after a meaningful status change. This is guidance,
not a Snodo timeout contract. Do not hold one long blocking wait: it delays
failure handling and makes it harder to keep other independent work moving. Do
not poll continuously; `get_job_status` and `list_jobs` are safe to call
repeatedly, but constant polling adds noise without making the coder finish
sooner.

Never infer completion from the `run_plan` response or from a validation pass.
The job's terminal status, exit code, plan task statuses, and any needed logs
are the evidence to act on.

## Route outcomes without bypassing judgement

The engine's canonical halt outcomes are `escalate`, `blocker`,
`validator_error`, `internal_error`, and `environment_error` (ADR 015). A
successful resolved task may merge only when auto-merge is enabled for its
protocol/mode and merge conditions are met. Auto-merge is opt-in; no task is
automatically landed merely because it ran. A resolved task with auto-merge
disabled (or no eligible task worktree) is recorded `completed`, although its
branch did not land on the base branch; the run reports why it stayed
unmerged. The plan status `unmerged` is used when an enabled merge attempt
fails. A merge conflict is an escalation and preserves the task
branch/worktree for resolution.

- **`blocker`**: a validator judged the work and rejected it. Never ask a human
  to authorize past it. Address the defect with a corrective follow-up task
  (or revise the spec when the spec itself is wrong), then let validators judge
  that new work. In plan task status this is `blocked`.
- **`escalate`**: park this task until a human reviews and authorizes it with
  `snodo authorize`. Do not wait indefinitely, self-authorize, or route around
  the gate. Keep processing independent work that does not depend on the parked
  task, and surface the decision needed. In plan task status this is `blocked`.
- **`validator_error`**, **`internal_error`**, and **`environment_error`**:
  these are operational failures, not verdicts on the task's content. Surface
  the error and its logs, address the validator/engine/environment problem, and
  only then decide whether the recorded job is appropriate to retry. Plan task
  status records these as `errored`; do not feed an operational failure back as
  a critique of the task spec.
- **Clean resolved work**: check whether it actually merged. When auto-merge
  is enabled and succeeds, the task lands on the resolved base branch. If it is
  not enabled or isolation was degraded, the task can be `completed` while its
  branch remains off the base branch; report the run's unmerged detail and get
  attention rather than claiming it landed. `unmerged` is the plan status when
  an eligible auto-merge was attempted and failed; inspect that outcome and
  leave the branch for attention.

The plan's `completed`, `blocked`, `errored`, and `unmerged` statuses are plan
tracking values, not new halt types. In particular, `record_task_status` writes
an operator's account outside the engine loop and marks it unjudged. It cannot
replace validators, establish a validator pass, or prove that work ran or
merged. Never use it to manufacture completion on an unattended run.

## Fix forward; do not spin

If a defect becomes clear while a task is running, let that run reach its
terminal result. Then create a corrective task with a focused spec and a
dependency on the affected work where appropriate. This preserves the original
judgement and its evidence while the follow-up is independently judged. Do not
stop or revert an in-flight task merely because a later correction is needed.

Do not blindly call `retry_job` in a loop on a halted task. First inspect the
job status, logs, plan status, and halt outcome. A task halt is not one generic
failure: fix a blocker forward, park an escalation for its human decision, and
resolve an operational fault before choosing a retry. A retry preserves the
recorded spec by default; change that spec only when the diagnosis supports a
change.

## Leave a trail for the returning human

At each intent boundary, leave a concise record that answers:

- What intent, plan, and job were processed?
- Which tasks ran, and which changes actually merged?
- Which work is parked or unmerged, with the outcome and reason?
- What human authorization, operational repair, or corrective task is next?
- Which independent work continued while another task was parked?

Report unresolved items explicitly. A successful job response, a recorded
status, or a finished coder log alone is not evidence that every change landed.
