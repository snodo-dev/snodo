# Following a plan run

This page is a short plan-specific index. The canonical live-following
instructions are the MCP guide topic [`following-a-run`](following-a-run.md),
which describes cloud live view, CLI log watch, and host-dependent MCP Apps
support. Use this page for plan hierarchy and final task outcomes; do not treat
the optional Apps panel as the primary watch path.

`run_plan` is asynchronous. It returns the plan-run job's `job_id` as soon as
the run is accepted; that response does not mean that any task passed. Keep the
plan name and job id together in the orchestrator's run record.

## Build the in-flight view

Call `get_plan` with the plan name for the source-of-truth plan view. Its
`waves` field shows the graph, `tasks` is a map from task id to the current plan
status, and `task_runs` joins tasks to their latest child job when one exists.
Read it during the run as well as after it finishes.

Use `list_jobs` to see the job rows and connect the hierarchy:

1. Find the plan-run row by its returned `id`. It has `plan` set and an empty
   `task_ref`.
2. Find child rows whose `parent_job` equals that plan job id. Their
   `task_ref` identifies the task they ran.
3. For a child that needs detail, call `get_job_status` with its `id`. This
   gives its status, exit code, timestamps, and task specification. Call
   `get_job_logs` with the same id when the task needs diagnostic output.

Do not require every task to have a child row. A task that ran inline has no
child job; its task status is still in `get_plan`, and its output is in the
plan job's own `get_job_logs` result. The plan job is the parent row, not a
task outcome.

## Follow the run live

When cloud sync is configured and the job has a task reference, suggest the
cloud live view at `<configured cloud liveness/app URL>/now?task_ref=<task_ref>`
for task status and progress. It does not stream output. Always suggest
`snodo logs <job_id> --watch` for the live output stream.
`watch_job(job_id)` is an optional MCP observer:
it returns a plain-text snapshot for clients without MCP Apps support, and some
hosts render its refreshable panel. Use `get_plan` when you need the per-task
map, and refresh `list_jobs` only when you need to discover child jobs.

There is also an opt-in narrated path: call `run_plan` with `wait=true` and
send a progress token with the MCP request. The server emits a line when a
task's plan status changes, including the child job id when there is one. This
blocks that tool call until the run ends or its `timeout` expires; without a
progress token, prefer the immediate-return path and follow the cloud view and
CLI log watch when available.

## Know when to stop

Job status is separate from plan task status. A job moves through `queued` and
`running`, then reaches one of the terminal statuses `completed`, `failed`,
`cancelled`, or `unmerged`. Stop polling the plan job when it has one of those
terminal values. Also inspect its `exit_code`; `completed` with exit code `0`
is the successful job result, not merely any terminal result.

Do not close the live view because `run_plan` returned, because validation
passed, or because one child finished. Conversely, do not wait forever for all tasks to say
`completed` after the parent job is terminal: a stopped or failed run can leave
tasks pending or record a non-success outcome. Once the parent is terminal,
read the final plan and classify every task before choosing a follow-up.

The plan task vocabulary is `pending`, `in_progress`, `completed`, `blocked`,
`errored`, and `unmerged`. `pending` and `in_progress` are not final task
outcomes. `completed`, `blocked`, `errored`, and `unmerged` are terminal plan
task outcomes. Do not translate these into new halt types or status values.

## Read the final outcome

The final `get_plan` response includes `run_summary` for the latest persisted
run: per-task outcome, halt type, attempts, duration, cost, and delivery, plus
run totals. It contains no justifications or logs. For detailed reporting,
including tokens and cost provenance, use `snodo plan status <name>
--run-summary`; add `--json` for machine-readable output.

After the plan job reaches a terminal status:

1. Call `get_plan` one final time and record each task's status from `tasks`.
2. For each task with a child in `task_runs` or `list_jobs`, use its child job
   id with `get_job_status` to record the exit code and timing. Read
   `get_job_logs` for a failed, cancelled, or unmerged child, or whenever the
   reason is not already clear.
3. For an inline task, use the plan job's status and exit code as the job-level
   evidence and read the plan job log for that task's output. Do not fabricate
   a child job id.
4. Record which tasks completed, which are blocked or errored, which are
   unmerged, and which remain pending. Report the plan job id, task ids, job ids
   where present, exit codes, and the evidence for any follow-up.

The final plan map is the per-task answer; the job status and logs explain the
execution evidence. Keep both. A successful parent job alone is not proof that
every task completed or that every change merged.

Plan tasks use a plan-owned integration branch. Passing tasks merge into it
before their wave is complete, so dependent waves see earlier changes. After
every wave succeeds in an unfiltered run, the runner delivers that branch using
the active mode's `execution.delivery` setting:

- **`local_merge`:** merge the integration branch into the base branch, then
  remove the integration branch and its linked worktree.
- **`push_branch`:** push the integration branch to the configured delivery
  remote; the branch and its linked worktree remain available locally.
- **`change_request`:** push the integration branch and open one change request
  into the base branch; the branch and its linked worktree remain available
  locally.

A failed, partial, stopped, or `--wave`-filtered run does not deliver the
integration branch; it remains available for the next run to resume. If delivery
itself fails, the run exits non-zero and keeps the branch and linked worktree for
resolution. A successful push or change-request delivery also keeps both locally;
only a successful local merge removes the integration branch and worktree.
