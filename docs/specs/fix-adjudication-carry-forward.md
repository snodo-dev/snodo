# Fix: adjudication carry-forward on task retry

## Intent
When a task is blocked, adjudicated, and re-dispatched (new task ID),
the decision record doesn't carry forward. The adjudication was minted
for task_69fa7f but the retry creates task_61f823 — a new task with a
new ID. The engine looks up adjudications by task_id, finds nothing for
the new task, and the validator blocks again. The human adjudicated the
concern; the protocol should respect that across a retry.

## What to change

### Two options — pick the less invasive:

(A) Session-scoped adjudications: store adjudications keyed by
    (session_id, validator_id, decision) not just task_id. When the
    engine looks up adjudications for a task, it also checks
    session-scoped records for the same validator. A session-scoped
    adjudication says "for this session, this validator's warn is
    overridden" regardless of which task triggered it.

(B) Carry-forward on re-dispatch: when the orchestrator re-dispatches
    after an adjudication, it includes the decision record ID in the
    task spec (the ADJUDICATION field already exists in the task spec
    pattern — seen in the dogfood). The engine reads this field and
    pre-loads the decision record for the new task.

Option B is already partially implemented — the dogfood spec included
"ADJUDICATION: task_69fa7f → proceed (Record ID: 8b141ff2fca5fee2)" in
the task spec text. The engine needs to parse and apply this field
rather than ignoring it.

Read the current adjudication lookup in policy.py and engine/loop.py
(how decision_records are matched to task_ref) before deciding which
option is cleaner. Recommend the one that requires fewer changes.

## Acceptance criteria
- Adjudicating a blocked task and re-dispatching does not require
  re-adjudicating the same validator concern
- The decision carries forward to the retry task
- The audit trail still records both the original adjudication and
  its application to the retry

## Constraints
- Read policy.py (find_adjudicated, how task_ref is matched),
  engine/loop.py (how decision_records are loaded and passed),
  cli/commands/adjudicate_cmd.py before touching anything
- Do not change the JWT structure or signing
- The carry-forward must be auditable — clear in the logs which
  adjudication authorized which task
