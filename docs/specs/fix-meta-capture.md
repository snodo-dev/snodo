# Spec: fix usage correlation + halt persistence (meta shows zeros)

Two distinct writer/reader mismatches make snodo meta show Tokens 0 +
"no halt data" on every real job.

## Bug 1 — usage job_id correlation
Writer (coders/litellm.py ~213, validators/llm_validator.py) puts
job_id/task_id in the TOP-LEVEL metadata kwarg. UsageTracker reads it
from kwargs["litellm_params"]["metadata"] (usage_tracker.py:45). Never
matches -> job_id="unknown" -> _persist_usage bails (79-81) -> nothing
written.

Fix: make writer and reader agree. litellm nests user metadata under
litellm_params at call time, so the READER location is likely correct
for how litellm forwards it. Confirm where litellm actually places the
metadata in the callback kwargs, then align:
- If litellm forwards top-level metadata into
  litellm_params.metadata -> reader is right, but our value isn't
  arriving there. Check we pass metadata in the form litellm
  propagates to the callback.
- Simplest robust fix: in UsageTracker, read job_id from BOTH
  locations: kwargs.get("litellm_params",{}).get("metadata",{}) OR
  kwargs.get("metadata",{}). Take whichever has job_id.
Verify with a real run: usage[] in the job's state.json is non-empty
with job_id == the real j_ id.

## Bug 2 — halt payload never flushed to state.json
Writer: engine/loop.py _auto_write_halt_payload -> 
session.checkpoint.decisions["halt"] (in-memory/checkpoint).
Reader: snodo meta -> .snodo/jobs/<id>/state.json.
wrapper.py (60-68) writes only status/exit_code/timestamps to
state.json, never the halt.

Fix: at job completion, persist the halt payload INTO state.json.
In jobs/wrapper.py where final state is written, extract the halt
decision from the session checkpoint (or have the loop return it) and
write state["halt"] = <halt payload> alongside status/exit_code.
meta already reads halt from state.json — once it's there, the
"highlight" works.

## Tests
- a completed job's state.json has non-empty usage[] with correct job_id
- usage records carry per-call cost (catalog/litellm priced)
- state.json contains a "halt" key with final_decision +
  validator_results after a run
- snodo meta j_xxx shows real tokens, cost, and a highlight derived
  from halt (not "no halt data")

## Touch
infrastructure/usage_tracker.py (read job_id from correct/both
locations), coders/litellm.py + validators/llm_validator.py (only if
the metadata needs reshaping to reach the callback), jobs/wrapper.py
(flush halt payload to state.json), engine/loop.py (expose halt to
wrapper if not already reachable)

Commit: fix(meta): correlate usage job_id + flush halt payload to state.json
