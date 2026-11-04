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

### End-of-run usage summary

At the end of a task run, Snodo prints a compact summary with a row for each
role (the coder and each validator) followed by a `total` row, but only when the
task recorded at least one usage record. If its task state has no usage list, an
empty list, or no dictionary records, no task summary is printed. A recon prints
a summary with a row for each recon agent followed by a `total` row whenever it
has at least one result, even if an agent has no usage data; if the recon has no
results, it exits before printing a usage summary. The columns are input tokens,
output tokens, model-call time, and output tokens per second. For example, a task
summary can look like:

```text
Usage: role | input tokens | output tokens | model time | output tok/s
coder: 10 | 5 | 1.0s | 5.0 C
validator:test: 20 | 4 | 1.0s | 4.0 C
total: 30 | 9 | 3.0s | 4.5 C
```

A recon summary uses the recon agents' model names as roles; an agent without
token or timing data can still appear:

```text
Usage: role | input tokens | output tokens | model time | output tok/s
model-a: 10 | 4 | 1.0s | 4.0 C
model-b: 20 | 6 | 2.0s | 3.0 C
model-c: n/a | n/a | n/a | n/a
total: 30 | 10 | 9.0s | 3.3 C
```

`C` means Snodo calculated the rate from output tokens divided by time spent
in model calls; `R` means the provider reported the rate. No provider reports a
rate today, so current rates are calculated. The total time shown for a task is
its elapsed run time; recon total time is recon wall time, while rates are based
on model-call time. `n/a` means the relevant summary value was not reported or could not be
measured, not zero. Input-token counts from the in-place `opencode-cli` coder and
the LiteLLM coder are counted differently and are not directly comparable.

## Interpret the result

Job status is separate from plan task status. A job moves through `queued` and
`running`, then reaches one of the existing final statuses: `completed`,
`failed`, `cancelled`, `unmerged`. `completed` with exit code `0` confirms a
successful job; a final state with another exit code is not success.

For a plan run, use `get_plan` when you need the per-task status map and task
relationships. A host-rendered panel, when supported, follows the plan-run
job's own stdout log. For a queue run, it follows the queue-run job's own output
and final status.
Use `list_jobs` to inspect compact child-job summaries (default limit 50, maximum
100; optionally filter by status). Check `truncated` and increase the limit if
needed, then use child ids with `get_job_status` and `get_job_logs` for detail.

Do not infer task outcomes from the parent job alone. Keep the plan task
vocabulary (`pending`, `in_progress`, `completed`, `blocked`, `errored`, and
`unmerged`) distinct from job status, and do not translate either into new
status or halt values.

## Retry starting point and retained work

`snodo run --retry <task_id>` continues the previous attempt's work by default,
including commits made before a halt and uncommitted changes. Rerunning a wave
also reuses its task workspace. The coder receives a continuation notice when
that workspace already carries work; it still needs validation.

Choose `snodo run --retry <task_id> --fresh-start` or
`snodo plan run <plan> --wave <id> --fresh-start` for a clean checkout of the
task's base revision (the plan integration branch for plan tasks). This is the
same choice as `fresh_start: true` on MCP `retry_job`. Fresh does not mean
remove: earlier branches, worktrees and records remain readable. The run names
what was left untouched. The status records `starting_revision`,
`starting_branch`, and `inherited_work`; `snodo task show <task_id>` displays
the starting-point evidence after a halt. Older attempt snapshots live under
`.snodo/tasks/<task_id>/attempts/`.

Preserved worktrees live beside the repository in
`../.snodo-worktrees/<task_id>/`, or
`../.snodo-worktrees/<plan>/<task_id>/` for plan tasks. Fresh workspace names
include `_fresh_` and are printed by the run. They accumulate on that machine
across runs; they are not temporary directories automatically erased after a
halt. Use `snodo worktree list` to inspect them. The explicit discard command
is `snodo worktree remove <identifier>` (use the plan-scoped identifier from
the list). It removes the worktree and its branch, not the saved task records.
