<!-- snodo-guide topic="following-a-run" aliases="run,follow-run" summary="Follow an asynchronous job in a live MCP Apps view" section="# Following a run" -->
# Following a run

`dispatch_task`, `run_plan`, and `queue_run` return a job id when background
work is accepted. The return only confirms that the run was queued; it does not
mean the work passed or completed.

## Open the live view

After any of those tools returns `job_id`, call `watch_job(job_id)`. In MCP Apps
hosts such as Claude or ChatGPT, the result opens a live panel with the job id,
status, elapsed time, and the latest ten stdout lines. The panel refreshes every
few seconds through `get_job_status` and `get_job_logs`, then stops automatically
at the job's final status and shows its final outcome. Keep the panel open while
the job runs; the orchestrator does not need to schedule repeated status calls.

The text result contains the current status and recent output for clients that
do not render MCP Apps. `watch_job` uses only the same job status and stdout
logs already available through the dispatch capability. For a particular
follow-up, call `get_job_status` or `get_job_logs` directly.

## Interpret the result

Job status is separate from plan task status. A job moves through `queued` and
`running`, then reaches one of the existing final statuses: `completed`,
`failed`, `cancelled`, `unmerged`. `completed` with exit code `0` confirms a
successful job; a final state with another exit code is not success.

For a plan run, use `get_plan` when you need the per-task status map and task
relationships. The live panel follows the plan-run job's own stdout log. For a
queue run, the panel follows the queue-run job's own output and final status.
Use `list_jobs` to inspect child jobs, then their ids with `get_job_status` and
`get_job_logs` when you need child-task detail.

Do not infer task outcomes from the parent job alone. Keep the plan task
vocabulary (`pending`, `in_progress`, `completed`, `blocked`, `errored`, and
`unmerged`) distinct from job status, and do not translate either into new
status or halt values.
