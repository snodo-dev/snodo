# snodo authorize: list pending decisions when called with no task_id

## Intent
snodo authorize <task_id> requires knowing the task_id upfront. Users
shouldn't need to grep job logs to find it. When called with no
arguments, authorize should list all pending decisions in the current
session so the user can see what needs authorization and pick one.

## What to change

### cli/commands/authorize_cmd.py
Make task_id optional (not required). When absent:
- Resolve project root (cwd)
- Load active session (current_mode from state.json → get_active_session)
- Read checkpoint.decisions["pending_decisions"]
- If empty: print "No pending decisions in current session."
- If present: print a table:

  Pending decisions in session sess_xxx (producer):

  TASK ID        TYPE           TARGET                    JUSTIFICATION
  task_fca4bb    adjudicate     architecture-validator    Spec contains...
  task_abc123    set_model      validator:security        Better performance

  Run: snodo authorize <task_id> to review and sign.

### cli/main.py
Update the authorize command registration to make task_id optional
(nargs='?' or Optional[str]).

## Acceptance criteria
- snodo authorize (no args) lists pending decisions
- snodo authorize <task_id> still works as before
- Empty session → clear "no pending decisions" message
- Table shows enough context to pick without looking anything up:
  task_id, type, target (validator_id or scope), justification preview
- Resolves the active session the same way authorize already does

## Testing
- Unit: no pending decisions → correct message
- Unit: pending decisions → correct table output
- Unit: task_id provided → existing behavior unchanged
- Full suite passes

## Constraints
- Read cli/commands/authorize_cmd.py, cli/main.py before touching
- Reuse the existing session resolution path — same as authorize with
  a task_id
- Keep it simple — this is a display command, no new storage
