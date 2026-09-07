# Spec: the plan layer reports the outcome the engine decided

## Root cause

The engine classifies every halt as **escalate**, **blocker**, **validator_error**
or **internal_error** and persists that in the halt payload (`halt_type`,
`final_decision`, `raw_halt_type` — a self-consistent four-outcome vocabulary, see
`engine/nodes/writeback.py:_CANONICAL_HALT` and the audit log). The plan runner
(`snodo/cli/commands/plan_run.py`) did not read any of it: it branches on the exit
code, prints `FAILED`, and records the task `blocked` — for every outcome.

Three distinct faults reached an operator that way in one day:

- a validator that exhausted its tool budget without producing a verdict, on work
  four other validators passed and whose test suite was green;
- a coder cut off by its tool's own provider deadline;
- a completed, verified task that lost a race for git's index lock.

Each has a different correct response — re-run, fix configuration, merge — and all
three printed identically, so telling them apart meant opening the JSON. That
teaches an operator to discount rejections in general, which is the wrong lesson
when the pre-execute validators have been catching real defects.

Recording an operational fault as `blocked` is not cosmetic either: a blocked task
routes the next attempt into the retry path with failure context, so a judge that
timed out becomes a critique handed to a coder that did nothing wrong.

## Fix

The reporting and status layer reads what the engine already computed and already
persisted. Nothing in the engine's classification changes; nothing in the audit
log changes.

### Report the outcome

`_execute_wave_task` (sequential), its retry branch, and the concurrent job polling
path read the canonical outcome for the task from the persisted halt payload and
print it in operator-actionable terms:

| outcome         | report line                      |
|-----------------|----------------------------------|
| `blocker`       | `[<id>] BLOCKED in …`            |
| `escalate`      | `[<id>] ESCALATED in …`          |
| `validator_error` | `[<id>] VALIDATOR ERROR in …`  |
| `internal_error`  | `[<id>] INTERNAL ERROR in …`   |

The outcome is resolved from the job's `state.json` halt record first (the engine's
own write), falling back to the session checkpoint's `decisions["halt"]`, and only
then to the generic line — so the name printed is the name the engine decided, and
a task with no recorded outcome keeps a sensible fallback.

### Record a distinct status for unjudged work

`PlannerMCP.update_status` gains the status value `errored`. The plan runner
records `blocked` only for tasks that failed judgement (the engine decided
`blocker` or `escalate`). A task whose halt was `validator_error` or
`internal_error` is recorded `errored` and never routes the next attempt through
the retry path with failure context: the next plan run starts it fresh with the
original spec, not with a critique of work that was never judged. A failed task
with no recorded halt outcome keeps today's `blocked` status.

`plan_cmd.py` renders `errored` tasks distinctly (`?` marker) and reports their
count in the plan summary.

## Scope

`snodo/cli/commands/plan_run.py`, `snodo/cli/commands/plan_cmd.py`,
`snodo/mcp/planner.py`, `tests/cli/test_plan_execution.py`,
`tests/mcp/test_planner.py`, `CHANGELOG.md`. No engine change; the audit log is
untouched (it already records `halt_type` / `final_decision`).

## Tests

- A run to each of the four outcomes prints the named line and records the status
  (`blocker`, `escalate` → `blocked`; `validator_error`, `internal_error` →
  `errored`).
- A `validator_error` run records `errored`, never consumes failure context, and
  hands the next attempt the original spec (not "Previous attempt failed …").
- The outcome resolves from the engine's job state.json before the session
  fallback.
- The concurrent job path names the engine outcome and records `errored`, never a
  generic `FAILED`/`blocked`.
- The outcome label helper maps all four outcomes and the no-record fallback.
- Existing plan execution, planner, run-cmd and status suites pass.

## Verify

`uv run pytest tests/ -q && uv run ruff check . && uv run lint-imports`

## Touch

`snodo/cli/commands/plan_run.py`, `snodo/cli/commands/plan_cmd.py`,
`snodo/mcp/planner.py`, `tests/cli/test_plan_execution.py`,
`tests/mcp/test_planner.py`, `CHANGELOG.md`, `docs/architecture.md`, this spec.
