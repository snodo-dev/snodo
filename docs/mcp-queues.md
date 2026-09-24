# MCP queue tools

The `queue` mode capability grants five MCP tools for operating the plan
queues defined by [ADR 053](decisions/053-plans-run-from-queues.md):

- `queue_list` lists queues in creation order and reports each plan's status.
- `queue_create` creates an empty named queue.
- `queue_move` reorders a queued plan or moves it to another queue; a running
  plan cannot be moved.
- `queue_validate` reports plan verification, queue readiness, order problems,
  cross-queue path warnings, and active runners without changing queue state.
- `queue_run` starts the CLI queue runner as an asynchronous job and returns a
  `job_id`. Follow it with `get_job_status` and `get_job_logs`.

Queue tool access follows the active mode grant. No queue tool requires a token
held by the caller; queue execution preserves the CLI's ordering, refusal, and
protocol-configured blocking behavior.
