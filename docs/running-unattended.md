<!-- snodo-guide topic="automation" summary="Intent-to-merged-work orchestration for long unattended runs" section="# Running Snodo unattended" -->
# Running Snodo unattended

This guide is for an orchestrator that takes a stream of intents and keeps
turning them into judged, recorded work without a person watching every tool
call. Follow the [smallest-structure rule](authoring-a-plan.md#1-the-one-modelling-rule)
for each intent. Snodo's execution loop and validators decide whether work passes. Treat
each run as a job with an outcome to inspect, not as a single call that
completes the intent.

## Notify the operator

Background job notifications are opt-in. Add one or more targets to
`~/.snodo/config.yml`; target URLs and tokens are secrets and `snodo config
show` redacts them. See the [user configuration reference](configuration.md)
for all user-level settings and notification target fields. `webhook` receives Snodo's generic JSON event, and `ntfy`
receives the short message as plain text. `slack`, `discord` and `teams` format
that same actionable message for their respective incoming-webhook endpoints.
For Slack and Discord, use their incoming-webhook URLs. For Teams, use the URL
from a Power Automate/Workflows flow with the **When a Teams webhook request is
received** trigger; Snodo posts the trigger's documented message envelope with
an Adaptive Card attachment. See the [Slack incoming webhook
docs](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks),
[Discord webhook docs](https://discord.com/developers/docs/resources/webhook#execute-webhook),
and [Teams webhook trigger docs](https://learn.microsoft.com/en-us/connectors/teams/#when-a-teams-webhook-request-is-received)
for their current payload requirements. Each URL can be stored as an
environment reference (`env:VARIABLE_NAME`) rather than a literal:

```yaml
notifications:
  targets:
    - type: ntfy
      name: phone
      url: https://ntfy.sh/my-private-topic
    - type: webhook
      name: team-chat
      url: env:SNODO_GENERIC_WEBHOOK
    - type: slack
      name: slack
      url: env:SNODO_SLACK_WEBHOOK
    - type: discord
      name: discord
      url: env:SNODO_DISCORD_WEBHOOK
    - type: teams
      name: teams
      url: env:SNODO_TEAMS_WORKFLOW_WEBHOOK
      token: env:SNODO_TEAMS_TOKEN # only when the Workflow trigger requires authentication
  events:
    - job_finished
    - task_halted
    - authorization_needed
    - job_silent
  silence_threshold_seconds: 900
```

Targets may be `webhook`, `ntfy`, `slack`, `discord` or `teams`; an optional
`token` is sent as a bearer authorization header. For Power Automate triggers
configured to accept anonymous calls, leave `token` unset. `events` can select
any subset of the four shown event names. With no targets configured no
notification work is done. Messages name the project, job, plan/task when
known, outcome, and `snodo logs <job_id>` (or
`snodo authorize` for a pending human decision). Delivery is detached from the
runner, bounded, and best-effort. Verify all configured targets with
`snodo notify test`. Notifications reach the operator; they do not wake or
resume the orchestrating agent. An unattended agent must keep its own process,
watch, and next action alive rather than relying on a notification to restart it.

For plans organized into queues, start an orchestration pass with
`snodo queue validate [x]`. It reports whether each queue's front plan can
run, re-verifies every queued plan, flags visible path dependencies and
cross-queue file collisions, and shows whether a runner currently holds each
queue's lock. It is a report only: it does not change queue or plan state. Use
`--json` when the orchestrator needs a machine-readable result. Reorder or
unblock stopped work before starting the queue run.

For the queue progression loop, including how to unblock a stopped front and
when to opt into non-blocking or parallel runs, see [Keep queues moving](queues.md).

## The loop: intent to landed work

Plans ready for unattended progression are kept in named queues. Use
`snodo queue` to list queues in creation order, including each queued plan in
run order and its status from that plan's records. Create an independent queue
with `snodo queue create <name>`. Reorder or move a queued plan with
`snodo queue move <plan>`; `--front`, `--before <plan>`, and `--after <plan>`
select its position, while `--to <queue>` selects a destination (the back by
default). Moving a plan that is running is refused. These commands accept
`--json` for the versioned machine interface.

For an intent that is one task, call `validate_task`, then `dispatch_task`, and
follow the returned job with `watch_job`; do not wrap it in a one-task plan.
When a task is confined to one declared module, name it with `module` on
`dispatch_task` or `generate_spec`; its module test command is selected and its
writable paths are bounded. For multi-wave work, use the plan workflow below:

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
5. Call `watch_job(job_id)` and hand the operator its browser link when one is
   returned. Otherwise, use `snodo logs <job_id> --watch`. Inspect `get_plan`
   for per-task status and validation detail; use `get_job_status` or
   `get_job_logs` for specific follow-up. Read the terminal result and task
   outcomes before choosing the next intent.
6. Record what happened in a durable operator-readable report (or the
   orchestrator's run log): the intent and plan/job ids, what ran, what landed,
   what is parked, why, and any follow-up needed. Do not rely on a later human
   reconstructing this from a conversation.

`validate_plan` checks structure and plan-time references; it is not a separate
human authorization gate. Plan tasks use the same execution validators and
auto-merge policy as direct tasks. A plan does add durable plan→wave→task
history that the cloud can reconstruct under ADR 054, while direct task history
has no plan hierarchy. Prefer the direct path for one task unless preserving
that hierarchy is an intentional reporting requirement.

Plan and job vocabularies are different. Job status is `queued`, `running`,
`completed`, `failed`, `cancelled`, or `unmerged`. Per-task plan status is `pending`,
`in_progress`, `completed`, `blocked`, `errored`, or `unmerged`. A finished job
is not necessarily a successful plan: inspect its `exit_code`, logs, and task
statuses. `get_plan` can be read while a run is in progress as well as after.

## Keep the orchestration alive

`watch_job` is the live path for job progress; it does not keep an unattended
agent alive. Keep the agent's own watch/action loop running, handle terminal
results, and schedule its next action. Notifications are operator-facing
signals, not agent wake-ups.

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
fails. A merge conflict is a merge outcome, not an engine halt escalation: it
is recorded as `merge_conflict_escalated`, leaves the task `unmerged`, and
preserves the task branch/worktree for resolution.

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
terminal result. Then put the correction in a later wave of the same plan, or
create a new plan with a focused spec if the original plan has already ended.
Plan dependencies connect waves within one plan; a task in a new plan cannot
depend on a task from another plan. This preserves the original judgement and
its evidence while the follow-up is independently judged. Do not stop or revert
an in-flight task merely because a later correction is needed.

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
