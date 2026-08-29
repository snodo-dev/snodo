# Spec: corrupt job state.json is refused, preserved, ever-overwritten

## Root cause
`_merge_into_job_state` in `engine/nodes/writeback.py` treats a parse failure of a job's
`state.json` as "no state" and rewrites the file containing only the incoming `updates`.
That discards the halt payload, the per-turn tool telemetry (#105), and every other field
recorded for that job. The S110 warning makes it look handled; it prevents nothing.

`state.json` is the job's own record. A corrupt read must never be resolved by overwriting it.

## Fix
On a parse failure (unparsable JSON, or JSON that is not an object):

1. Do not write.
2. Preserve the original file by moving it to `state.json.corrupt-<timestamp>`.
3. Record the fault in the audit log as a `job_state_corrupt` event (matching the
   `session_corrupt` pattern).
4. Raise `JobStateError` so the caller learns the merge did not happen. Callers that must
   tolerate a corrupt job state catch this explicitly — the default is refusal.

### Callers
`_auto_write_halt_payload` and `_auto_write_classification` call `_merge_into_job_state`
and do not suppress exceptions, so `JobStateError` propagates to the node/loop — the default
is refusal. No caller opts in to tolerate.

### Scope
`packages/snodo-engine/src/snodo/engine/nodes/writeback.py` only. The per-turn telemetry
writers (`infrastructure/tool_telemetry.py`, `coders/litellm.py`, `validators/llm_validator.py`)
share the swallow-a-parse-failure shape but are out of scope; they remain best-effort sinks.

## Tests
- corrupt `state.json` is NOT overwritten; the original bytes survive in place
- corrupt `state.json` is preserved under `state.json.corrupt-*`
- a `job_state_corrupt` audit event is appended (job_id, state_file, preserved_as, error)
- `JobStateError` is raised
- a valid `state.json` still merges normally

## Verify
`uv run pytest tests/ -q -n auto -m "" && uv run ruff check . && uv run lint-imports`

## Touch
`packages/snodo-engine/src/snodo/engine/nodes/writeback.py`,
`tests/engine/test_writeback_coverage.py`,
`CHANGELOG.md`, this spec.
