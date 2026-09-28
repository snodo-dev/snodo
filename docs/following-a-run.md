<!-- snodo-guide topic="following-a-run" aliases="run,follow-run" summary="Follow an asynchronous job live" section="# Following a run" -->
# Following a run

`dispatch_task`, `run_plan`, and `queue_run` return a job id when background
work is accepted. The return only confirms that the run was queued; it does not
mean the work passed or completed.

## Watch a job live

After any of those tools returns `job_id`, call `watch_job(job_id)`. Its text
snapshot is available even when the host does not render MCP Apps. When the
server has a reachable browser URL, hand the operator the returned watch link:
it opens a read-only live page with job status, elapsed time, and the latest ten
stdout lines, and stops at the final status. The operator can open it in any
browser, including on a phone. The capability link is scoped to that job and
expires after 24 hours by default (`SNODO_WATCH_LINK_TTL` configures the lifetime
in seconds).

The MCP Apps panel is an optional extra, rendered only by some hosts; other
hosts, including Claude Desktop and relayed sessions, may return only the text
snapshot. Do not depend on the panel. When there is no reachable browser URL
(for example stdio or an ssh-proxied server), use `snodo logs <job_id> --watch`
for live output.
For streamable-HTTP behind a self-managed public proxy, set
`SNODO_PUBLIC_BASE_URL` to its public origin; `snodo serve --tunnel` detects its
managed tunnel hostname automatically.

The text result contains the current status and recent output for clients that
do not render MCP Apps. The browser page uses only the same job status and
redacted stdout logs already available through the dispatch capability. For a
particular follow-up, call `get_job_status` or `get_job_logs` directly.

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
