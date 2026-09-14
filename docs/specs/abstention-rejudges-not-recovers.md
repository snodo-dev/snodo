# An abstention re-judges the judge, not the coder

> **Superseded by [ADR 045](../decisions/045-a-judge-is-made-to-decide.md).**
> This spec describes the abstention state, which has been removed. A judge is
> now made to decide at the boundary of its budget, and a judge that still
> returns nothing is an error that fails closed. Kept as the historical record
> of the remedy that was wrong.

## Root cause

`_post_validate_node` (`engine/nodes/validation.py`) routes a HALT or ESCALATE
from the post-execute policy to `_spawn_recovery_subtask` (`engine/loop.py`)
whenever `_is_recoverable` is true. `_is_recoverable` only rejects non-error
*blockers* without a severity cap; an abstention (`severity is None`) passes,
so it spawns a recovery subtask: a fresh spec, a full pre-execute quorum, and a
coder dispatch. But an abstention is a judge reporting that it did not reach a
verdict. Nothing about the code was found at fault, so the recovery spec
describes a failure that was never diagnosed and the coder re-runs committed
work.

The stall check does not catch it because `_verdict_signature`
(`engine/loop.py`) compares `(validator_id, severity, justification)`, and
abstentions carry different prose each time.

## Fix

### 1. Re-judge in place, before recovery

In `_post_validate_node`, after the first policy evaluation, if the policy
halted or escalated *solely* on abstentions — no `error`, no `warn`, no
`blocker` — re-run the abstaining post-execute judges on the unchanged work and
re-evaluate. No coder dispatch, no recovery subtask, no task branch. This lives
in the validation node, not in the recovery machinery, because recovery exists
to change code and there is nothing to change.

### 2. Bound the retry

The retry ceiling is the mode's `max_recovery_depth` — the number the operator
already sets for "how many attempts may this cost". A protocol with
`max_recovery_depth: 0` permits no re-judging. No second config key.

### 3. Repeated abstention is a stall

`_verdict_signature` canonicalises an abstention to `(validator_id, "abstain",
"")`, dropping the justification. Two abstentions by the same judge compare
equal, so a judge that abstains again on unchanged work stops the retry instead
of looping. One shared `verdict_signature_from_results` is used by both the
recovery stall check and the re-judge, so they cannot disagree.

### 4. Name the terminal outcome

When the budget is spent or a stall is detected: `halt_type` is
`abstention_exhausted` or `abstention_stalled`. Both canonicalise to `blocker`
(ADR 015). The halt hint names the judge and its policy as the fix target, not
the code. The CLI halt follow-up (`followup.halt_followup`, shared by
`snodo run` and `snodo task show`) offers `snodo authorize <task_id>` for an
abstention-only halt and the bare `snodo run --retry` for every other halt.

### 5. `abstention_policy` is unchanged

It still decides what an abstention means; the re-judge only changes what a
halt/escalation caused solely by abstentions does next. An abstention is never
converted to a pass.

### Scope

`engine/nodes/validation.py`, `engine/loop.py`, `engine/state.py`,
`engine/nodes/state.py`, `engine/nodes/writeback.py`,
`cli/commands/followup.py`, `cli/commands/run_cmd.py`,
`cli/commands/task_cmd.py`, and tests. ADR 042 records the decision.

## Acceptance criteria

- A post-execute abstention on committed work re-judges that work without
  dispatching a coder (no `subtask_spawned`, exactly one coder dispatch in a
  full-graph run).
- Repeated abstentions with differing justifications trip the stall path and
  halt under `abstention_stalled`.
- The retry is bounded by `max_recovery_depth`; at 0 it halts immediately under
  `abstention_exhausted`.
- `abstention_policy` semantics are unchanged: `non_blocking` still excludes the
  abstention from the counts, `blocking` still halts.
- A warn or blocker alongside an abstention still drives recovery.
- The CLI follow-up offers `snodo authorize` for an abstention-only halt and a
  coder retry for a genuine blocker.

## Verify

`uv run pytest tests/ -q -n auto -m "" && uv run ruff check . && uv run lint-imports && uv run python scripts/enforce_file_length.py`
