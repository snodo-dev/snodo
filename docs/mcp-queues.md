# MCP queue tools

The canonical queue operating guide is [Keep queues moving](queues.md), the
`queues` MCP guide topic. This page is only a brief tool index; it does not
duplicate the queue run/recovery guide.

The `queue` mode capability grants six MCP tools for operating the plan
queues defined by [ADR 053](decisions/053-plans-run-from-queues.md):

- `queue_list` lists queues in creation order and reports each plan's status.
- `queue_create` creates an empty named queue.
- `queue_move` reorders a queued plan or moves it to another queue; a running
  plan cannot be moved.
- `queue_remove` removes a plan from whichever queue holds it without changing
  its plan records; a running plan cannot be removed.
- `queue_validate` reports plan verification, queue readiness, order problems,
  cross-queue path warnings, and active runners without changing queue state.
- `queue_run` starts the queue runner as an asynchronous job and returns a
  `job_id`. Follow it with the browser watch link when available, or
  `snodo logs <job_id> --watch`; `watch_job` also returns a text snapshot and
  may render an MCP Apps panel in supporting hosts.

Queue tool access follows the active mode grant. No queue tool requires a token
held by the caller; queue execution preserves the CLI's ordering, refusal, and
protocol-configured blocking behavior.
