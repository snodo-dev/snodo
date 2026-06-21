# Spec: direct-to-job-store writes for usage/halt/classification (Option B)

## Root cause
Two stores never reconcile: engine writes halt+classification to SESSION
store (~/.snodo/sessions/), usage drops on job_id mismatch, wrapper
flushes only status to JOB store. meta reads JOB store. Data never meets.
wave.json works ONLY because it writes direct to the job/project store.

## Fix: write all three direct to job state.json at production time,
remove the session-store indirection + wrapper flush for this data.

### 1. Fix usage job_id correlation (usage_tracker.py:45)
Read job_id from BOTH kwargs["metadata"] AND
kwargs["litellm_params"]["metadata"] (litellm forwards user metadata
under litellm_params). Once job_id is correct, _persist_usage already
resolves the job dir and writes direct — zero wrapper involvement.
(Confirm: writer in coders/litellm.py:213 + validators put job_id in
top-level metadata; align reader to find it wherever litellm lands it.)

### 2. Thread job_id into GraphBuilder
GraphBuilder gets session_id today but not job_id explicitly. Pass
job_id at construction (build_protocol_graph) so nodes can resolve the
job dir. (project_root already passed for wave registry — reuse that
path resolution.)

### 3. Direct writes for halt + classification
_auto_write_halt_payload and _auto_write_classification: write to the
job's state.json directly (resolve job_dir via job_id + project_root,
atomic os.replace merge like _persist_usage), INSTEAD OF
session.checkpoint.decisions.
- Keep the session write ONLY if resume/orchestration needs it
  (recon found no dashboard/session-show dependency on these). If
  nothing reads them from session, drop the session write. If unsure,
  dual-write (session for resume + job for meta) — but prefer single
  job write if safe.

### 4. Strip wrapper.py
Remove wrapper's read-modify-write of halt/classification (the source
of the halt_type-null-x8 corruption and the race). Wrapper keeps ONLY
status/exit_code/completed_at. All metadata now written direct by the
engine during the run.

### 5. Fix the malformed halt structure
The halt_type-null-x8 was the wrapper corrupting the structure. With
direct write, _auto_write_halt_payload writes ONE clean halt object
(final_decision, phase, validator_results, blocker_reason) — verify
it's a single object, not per-iteration null entries.

## Tests
- completed BACKGROUND job: state.json has non-empty usage[] with
  correct job_id, per-call cost
- state.json has a single well-formed halt (final_decision set, not
  null) + flow_type + wave_id
- snodo meta j_xxx: real tokens, cost, highlight (not "failed: unknown"
  on a completed job)
- wrapper no longer touches halt/classification; no halt corruption
- inline path ALSO persists these (not just background) — test both

## Touch
infrastructure/usage_tracker.py (job_id both-locations),
engine/loop.py (job_id into GraphBuilder; _auto_write_halt_payload +
_auto_write_classification direct to job state.json),
coders/litellm.py + validators (confirm metadata placement),
jobs/wrapper.py (strip to status only),
build_protocol_graph (pass job_id)

Commit: fix(persistence): write usage/halt/classification direct to job state.json, drop session-store indirection
