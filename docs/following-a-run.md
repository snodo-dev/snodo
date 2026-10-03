<!-- snodo-guide topic="following-a-run" aliases="run,follow-run" summary="Follow an asynchronous job live" section="# Following a run" -->
# Following a run

`dispatch_task`, `run_plan`, and `queue_run` return a job id when background
work is accepted. The return only confirms that the run was queued; it does not
mean the work passed or completed.

## Watch a job live

After any of those tools returns `job_id`, call `watch_job(job_id)`. Its text
snapshot is available even when the host does not render MCP Apps. When cloud
sync is configured and the job has a `task_ref`, suggest the cloud live view at
`<configured cloud liveness/app URL>/now?task_ref=<task_ref>`. It shows task
status and progress, but not a live output stream. Always suggest
`snodo logs <job_id> --watch` for the live output stream in a terminal. Do not
hand the operator the MCP server's `/watch/` capability URL.

The MCP Apps panel is an optional extra, rendered only by some hosts; other
hosts, including Claude Desktop and relayed sessions, may return only the text
snapshot. Do not depend on the panel.

The cloud live-view host follows the configured cloud liveness/app URL; it is
not the MCP server's host. Cloud live view requires cloud sync configuration
and a task reference.

The text result contains the current status and recent output for clients that
do not render MCP Apps. The browser page uses only the same job status and
redacted stdout logs already available through the dispatch capability. For a
particular follow-up, call `get_job_status` or `get_job_logs` directly.

## Usage and cost

Snodo records per-call usage in the `usage` list in each job's
`.snodo/jobs/<job_id>/state.json`; task-level records are in
`.snodo/tasks/<task_id>/state.json`. Records include the requested model
(`model`) and, when the provider reports one, the served model
(`served_model`), provider, input/output token counts, cache read/write tokens,
duration, role, and outcome. Failed model calls are recorded as errors. A
`null` value means that field was not reported or could not be measured; in
particular, a missing served model does not mean the requested model was served.

For an at-a-glance job or task summary, run `snodo meta <composite_id>` (add
`--json` for machine-readable output). The composite ID is the job ID returned
when work is queued (`j_...`) or a task ID (`task_...`). `snodo meta` aggregates
tokens and cost, while the `state.json` usage records contain per-call model
details. For completed task run records, `snodo runs --json` lists them;
`--send` additionally sends those records to Snodo Cloud.

Cost is not always known. A LiteLLM call can carry a provider-reported cost or
Snodo can estimate it from its model catalog when token counts and catalog
prices are available (recorded as `cost_source: "estimate"`). In-place coder
records use cost only when the adapter reports it. A `null` cost means there
was no reported cost and no available estimate; subscription-based coder usage
can therefore have token and duration data without a monetary cost. Check
`cost_source` to distinguish provider-reported cost from an estimate.

## Interpret the result

Job status is separate from plan task status. A job moves through `queued` and
`running`, then reaches one of the existing final statuses: `completed`,
`failed`, `cancelled`, `unmerged`. `completed` with exit code `0` confirms a
successful job; a final state with another exit code is not success.

For a plan run, use `get_plan` when you need the per-task status map and task
relationships. A host-rendered panel, when supported, follows the plan-run
job's own stdout log. For a queue run, it follows the queue-run job's own output
and final status.
Use `list_jobs` to inspect child jobs, then their ids with `get_job_status` and
`get_job_logs` when you need child-task detail.

Do not infer task outcomes from the parent job alone. Keep the plan task
vocabulary (`pending`, `in_progress`, `completed`, `blocked`, `errored`, and
`unmerged`) distinct from job status, and do not translate either into new
status or halt values.
