# Spec: blocked plan tasks resume through the retry path

## Root cause
`_execute_wave_task` in `snodo/cli/commands/plan_run.py` marks a task "blocked" when
`_execute_task` returns non-zero, and `_execute_waves` stops the run. Re-running
`snodo run --plan` skips completed tasks and resumes — but a blocked task is re-executed
from scratch, discarding the failure context that `_auto_write_failure_context` persists
and that `snodo run --retry` was built to consume.

The operator's loop today: plan halts, they run `snodo authorize`, they re-run the plan,
and the task starts over as if nothing had been learned.

## Fix
When `_execute_wave_task` is about to run a task the status file already marks "blocked",
and failure context exists for it, execute it as a retry rather than a fresh dispatch.

- Context resolution reuses `_retry_task`'s: `_resolve_failure_context` prefers
  `decisions["task_failure"][id]` and falls back to `_failure_from_halt_record` — the same
  resolution `_retry_task` uses, not a reimplementation.
- The retry execution itself delegates to `_retry_task`, which builds the augmented spec
  (original spec + failed validators + files changed) and enforces `max_retries`.
- A task at `max_retries` is not re-executed; the abandon/override guidance is printed.
- With no failure context (or no session manager), the task runs fresh — today's behaviour —
  and the line says so, so the operator can tell the two apart.
- A completed task is still skipped.

### Scope
`snodo/cli/commands/plan_run.py` and `tests/cli/test_plan_execution.py` only. Other agents
own `plan_cmd.py`, `planner.py`, the templates and the docs.

## Tests
- a blocked task with failure context resumes as a retry (delegates to `_retry_task`)
- a blocked task with no context runs fresh and says so
- a task at max_retries is not re-executed and prints the abandon/override guidance
- a completed task is still skipped

## Verify
`uv run pytest tests/ -q -n auto -m "" && uv run ruff check . && uv run lint-imports`

## Touch
`snodo/cli/commands/plan_run.py`, `tests/cli/test_plan_execution.py`, `CHANGELOG.md`,
this spec.
