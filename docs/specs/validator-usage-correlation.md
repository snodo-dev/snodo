# Spec: thread job_id/task_id to validators for usage correlation

## Why
dad5a3f wired coder usage correlation but NOT validators. Validators
are built inside _dispatch_one with no session/task context, so
_job_id/_task_id stay "" → usage_tracker drops the record
(_persist_usage skips job_id=="unknown"). Every validator call's
tokens/cost is lost.

## Fix (per recon — context, not attribute injection)
Validators are constructed opaquely, so pass via ValidatorContext:

1. ValidatorContext gains job_id/task_id (or a metadata dict carrying
   them). Set where the context is built for validation —
   _validate_node and _post_validate_node in loop.py, using
   self._session_id and task.id (same source the coder uses at
   1147-1150).

2. LLMValidator.evaluate() reads job_id/task_id from the context and
   passes them into the metadata dict on its completion() calls
   (all three paths: _call_llm, _call_llm_structured, tool-loop),
   replacing the current self._job_id/_task_id defaults.

3. role stays "validator:<id>" as already implemented.

## Tests
- validator completion persists a usage record with the real job_id
  (not "unknown") → record IS written to job.state.json.usage[]
- coder correlation unchanged
- role tag preserved

## Touch only
engine/loop.py, engine/validators.py (context construction),
validators/llm_validator.py

Commit: fix(usage): correlate validator usage via ValidatorContext (was dropped as unknown job_id)
