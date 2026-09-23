<!-- snodo-guide topic="outcomes" aliases="halts" summary="What each outcome means and what an MCP orchestrator should do next" section="# Outcomes and recovery for MCP orchestrators" -->
# Outcomes and recovery for MCP orchestrators

An MCP orchestrator receives validator results and asynchronous job records; it
cannot use `snodo validate` or branch on that command's process exit-code table.
Use the outcome payload and job state as evidence, then choose the next action
below. A `dispatch_task` or `retry_job` acceptance is not a successful result:
poll `get_job_status`, and inspect `get_job_logs` when the result needs context.

## The five canonical halts (ADR 015)

These are the engine's canonical halt names. `pass` is a successful validation
result, not a halt. A validation result does not itself mean the dispatched
task completed; confirm the terminal job status and exit code.

### `blocker`

**Means:** A validator judged the task or its work and rejected it. The concern
is a verdict, not an operational failure. In plan records the corresponding
task status is `blocked`.

**Next:** Inspect the returned validator result and, when necessary,
`get_job_logs` and the current repository/worktree evidence. If the concern is
valid, create a focused corrective follow-up task and let validators judge that
work. Change the specification only when evidence shows the specification
itself is wrong. A reported stale premise can be false: check the premise
against the current source before changing either implementation or spec.

**Never:** Override a blocker, including with `propose_adjudicate`; a blocker is
non-overridable (INV3). Do not rewrite a spec merely to silence an unverified
validator claim, or retry the same task blindly in a loop.

**Operator:** `snodo authorize` is not a blocker escape hatch. Use it only if a
separate `escalate` outcome asks for a human decision.

### `escalate`

**Means:** A validator concern requires a human adjudication. No authorization
has been granted yet; the task is parked (plan task status `blocked`).

**Next:** Surface the validator, concern, and task ID to the operator and wait
for the human to review it with `snodo authorize <task_id>`. The MCP tool
`propose_adjudicate` can record an unsigned proposal when a proposal is useful:
it writes an `adjudicate` entry to the active session's
`pending_decisions[task_id]`, containing the validator ID, `proceed` or `halt`,
justification, `proposed_by: "agent"`, and timestamp. It returns `status:
pending` and the instruction to run `snodo authorize <task_id>`; that is a
proposal, not an authorized decision. The human CLI reviews and signs it.

**Never:** Self-authorize, treat a pending proposal as a signed decision, route
around the gate, or retry blindly while authorization is unresolved. Do not use
`propose_adjudicate` to override a `blocker`.

**Operator:** Required. `snodo authorize <task_id>` is the human review and
signing step.

### `validator_error`

**Means:** A validator failed to produce a verdict. This says nothing adverse
about the task's correctness and is not a decision for a human to adjudicate.

**Next:** Inspect `get_job_logs` and the error payload, resolve the validator or
provider problem, then retry the same task with `retry_job` when the job is a
task job and the issue is fixed. By default, `retry_job` uses the recorded spec
unchanged. It dispatches under the same task ID and reuses that task's existing
worktree, so prior attempt work remains available; inspect it rather than
reproducing it. `append_spec` adds guidance without replacing the spec. Use
`revised_spec` only when a diagnosed specification correction is actually
needed.

**Never:** Turn an operational failure into a spec critique, retry blindly in
a loop, or record a validator status yourself. `propose_adjudicate` and
`snodo authorize` do not repair a validator failure.

**Operator:** Involve the operator when the validator/provider issue needs
configuration, credentials, or runtime repair. `snodo authorize` is not needed
for this outcome.

### `internal_error`

**Means:** The engine encountered an internal fault; it is not a verdict on the
task.

**Next:** Inspect `get_job_logs` and the halt details, preserve the job ID and
evidence, and surface the fault for diagnosis. Once the cause is understood and
resolved, `retry_job` can retry a task job with its original spec by default;
it preserves the same task identity and available worktree contents. Use a
follow-up task only for a deliberate code change, not to disguise the engine
fault.

**Never:** Treat it as a blocker, rewrite the spec to address an engine fault,
retry blindly in a loop, or record a validator status in the validators'
place.

**Operator:** Escalate the engine fault to the operator for diagnosis or repair.
`snodo authorize` is not an internal-error recovery action.

### `environment_error`

**Means:** The coder could not be invoked because the execution environment is
not ready (for example, a required executable or container runtime is missing).
It is the fifth canonical halt, but not one of the validation results returned
by `validate_task`, which does not invoke a coder.

**Next:** Inspect the error hint and `get_job_logs`; repair the environment
where the job runs. Then retry the task with `retry_job` and the unchanged
recorded spec. The task ID/worktree reuse keeps earlier work available for
inspection. If the fault is not repaired, stop and report it rather than
spawning more attempts.

**Never:** Classify it as a task blocker or validator failure, alter the spec
to mask a missing runtime, retry blindly in a loop, or record a verdict/status
for validators.

**Operator:** The operator is needed to install or configure the missing
runtime/credential or otherwise repair the execution environment. This does
not call for `snodo authorize`.

## Job outcomes beyond the five halts

Job status and validator outcome are different fields. Check both
`get_job_status`'s `status` and `exit_code`; for plan jobs also inspect per-task
statuses (for example with `get_plan`). `get_job_logs` provides stdout/stderr
context. A job's terminal status alone does not prove that all work passed or
landed.

### `unmerged`

**Means:** The job wrapper assigns `unmerged` when the run exits with code 2:
the work finished, but did not land on the base branch. It is not a new halt or
a validator verdict. In a plan, `unmerged` can also be the task status when an
eligible auto-merge attempt failed. A plan task marked `completed` may also
have work that is not on the base branch when auto-merge was disabled; check
the plan/job detail rather than infer merge from that word alone.

**Next:** Inspect job status, logs, and the plan/task details. Keep the branch
and work available for review; arrange a corrective follow-up task if the
merge conflict or remaining work needs code changes. If the task branch itself
needs resolution, have the operator resolve it and then re-evaluate/merge the
work under the normal process.

**Never:** Claim the change landed, rerun the task just to merge it, discard
the unmerged work, or mark it completed in place of actual validator/merge
evidence. Do not retry blindly; a retry is for re-running a task, not merging
an existing branch.

**Operator:** The operator is needed when resolving a merge conflict or
deciding how to land the retained branch. `snodo authorize` applies only if a
separate validator `escalate` requires adjudication.

### A `completed` job with a non-zero `exit_code`

**Means:** This is an inconsistent success signal. The job wrapper in the
current code writes `completed` for exit code 0, `unmerged` for exit code 2,
and `failed` for other non-zero exit codes. Therefore it does not normally
produce `completed` with a non-zero code. If an orchestrator sees that
combination (for example in a mixed or stale record), it must not infer
success from `completed` alone.

**Next:** Re-read `get_job_status` and inspect `get_job_logs`; for plan jobs,
inspect each task's recorded result too. Preserve and report the conflicting
signals. After identifying the actual failure and fixing its cause, use
`retry_job` for a retryable task failure, or create a focused follow-up task
when additional judged work is required.

**Never:** Declare success, set a status to completed on behalf of validators,
or repeatedly retry without understanding the non-zero exit. Do not use
`propose_adjudicate` to resolve a process exit code.

**Operator:** Ask the operator to investigate when the status and exit code
remain inconsistent or the cause requires environment/engine repair.
`snodo authorize` is needed only if independent validator output is
`escalate`, never just because the exit is non-zero.

## Keep status and judgement separate

`record_task_status` records an operator's account outside the engine loop; it
does not create a validator verdict, prove that work ran, or prove that it
merged. Never use it to manufacture a pass or to replace the validators'
status. Use a focused, judged follow-up task for new corrective work, and do
not call `retry_job` repeatedly without first inspecting the outcome and
resolving its cause.
