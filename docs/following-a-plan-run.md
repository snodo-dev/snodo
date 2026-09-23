<!-- snodo-guide topic="following-a-run" aliases="run,follow-run" summary="Follow an MCP plan run to its final task outcomes" section="# Following a plan run" -->
# Following a plan run

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

## Poll with backoff

Poll `get_job_status` for the plan job after a short initial delay. Poll
`get_plan` alongside it to refresh the per-task map, and refresh `list_jobs`
when you need to discover newly spawned children. A practical cadence is a few
seconds initially, then 10, 20, 40, and 60 seconds, capped around a minute.
Reset to a short delay after a task status changes. Coders normally take
minutes, so continuous polling adds noise without making the run faster.

There is also an opt-in narrated path: call `run_plan` with `wait=true` and
send a progress token with the MCP request. The server emits a line when a
task's plan status changes, including the child job id when there is one. This
blocks that tool call until the run ends or its `timeout` expires; without a
progress token, prefer the immediate-return path and poll yourself.

## Know when to stop

Job status is separate from plan task status. A job moves through `queued` and
`running`, then reaches one of the terminal statuses `completed`, `failed`,
`cancelled`, or `unmerged`. Stop polling the plan job when it has one of those
terminal values. Also inspect its `exit_code`; `completed` with exit code `0`
is the successful job result, not merely any terminal result.

Do not stop because `run_plan` returned, because validation passed, or because
one child finished. Conversely, do not wait forever for all tasks to say
`completed` after the parent job is terminal: a stopped or failed run can leave
tasks pending or record a non-success outcome. Once the parent is terminal,
read the final plan and classify every task before choosing a follow-up.

The plan task vocabulary is `pending`, `in_progress`, `completed`, `blocked`,
`errored`, and `unmerged`. `pending` and `in_progress` are not final task
outcomes. `completed`, `blocked`, `errored`, and `unmerged` are terminal plan
task outcomes. Do not translate these into new halt types or status values.

## Read the final outcome

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
