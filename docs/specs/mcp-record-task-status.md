# Spec: `record_task_status` — an MCP surface for recording a task's status

## Why

An orchestrator can propose a plan, author its specs, validate it, start it and
read it. It cannot record that a task was finished, or abandoned, outside the
loop. The CLI can — `snodo task complete` writes the plan's `status.json` so
later waves advance — so the capability exists and only the machine-facing
surface is missing. An orchestrator that notices a task was done by hand had to
tell a human to run a command (issue #326).

## Contract

Add the MCP tool `record_task_status(plan_name, task_id, status, who, notes?)`.

- **Same vocabulary as the plan.** `status` is one of the statuses
  `PlannerMCP.update_status` already accepts
  (`pending`, `in_progress`, `completed`, `blocked`, `errored`, `unmerged`). No
  new status, severity, halt type or state is introduced.
- **Same effect on wave advancement.** The plan's `status.json` is written
  exactly as the CLI writes it, so a later wave advances from the record.
- **Same provenance.** The audit event carries `who`, when, and the optional
  `notes` (the "why"). The event is marked `judged: false`,
  `engine_judged: false`, `outside_loop: true`, so a reader can tell an
  operator's account from a status the loop itself wrote.
- **One implementation.** Both surfaces call `PlannerMCP.record_status`. The
  CLI's hand-complete path is rewritten onto it; there is no second copy that
  can drift. The status vocabulary lives in one place (`_check_status`), read
  by `scripts/enforce_vocabularies.py`.
- **Records, never decides.** A recorded `completed` is not a validator
  verdict, cannot stand in for one, and does not satisfy the engine's quorum.
- **Mutates nothing else.** Only the task's status entry and the audit event.

## Audit events

| status | event |
|--------|-------|
| `completed` | `task_completed_by_hand` (the event `snodo task complete` has always written) |
| anything else | `task_status_recorded_by_hand` (so `snodo task show` never reports a blocked/errored task as hand-completed) |

Both carry the same provenance fields.

## Refusals

- A status outside the vocabulary is refused, exactly as the CLI refuses it.
- An unknown plan, a missing `task_id`, or a missing `who` is refused.
- The audit log is written before the call returns; if it is unavailable the
  record is refused rather than silently dropped.

## Constraints

- Do not change what the CLI does or its flags.
- Do not add a state, severity, halt type or task status.
- Do not add a tool that mutates anything else about a plan.

## Tests

- Recording the same status through the tool and through the CLI leaves the
  same plan state and the same audit entry.
- An invalid status is refused.
- A wave advances from the tool-recorded status as it would have from a CLI
  record.
- The audit entry is unjudged and distinguishable from an engine completion.
