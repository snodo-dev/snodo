# Spec: UsageTracker reads SNODO_JOB_ID env var (fix token capture)

## Root cause (recon-confirmed)
1. job_id never propagates GraphBuilder -> LiteLLMAdapter; _job_id
   stays "" (loop.py:1072 creates fresh_coder, never sets _job_id).
   Callback metadata always says "unknown".
2. SNODO_JOB_ID env var IS set in the subprocess (wrapper.py:44) but
   UsageTracker NEVER reads it — only reads metadata. The env-var
   "fix" was half-wired (set, never read).
Result: every bg usage record gets job_id="unknown", _persist_usage
resolves .snodo/jobs/unknown (nonexistent), writes nothing.

## Fix — make the env var authoritative
usage_tracker.py: resolve job_id with this precedence:
  1. os.environ.get("SNODO_JOB_ID")   <- always correct for bg jobs
  2. metadata job_id (kwargs both-locations) <- fallback for inline
  3. "unknown"
The env var is process-scoped and set correctly per bg job, so it's
the reliable source. Metadata stays as fallback for the inline path
(no subprocess, no env var) — but ALSO set SNODO_JOB_ID for inline if
that path has a job id, so both work uniformly.

## Also fix the propagation (belt + suspenders, optional)
loop.py:1072: set fresh_coder._job_id = self._job_id (the GraphBuilder
HAS it). Cheap, makes metadata correct too. Do it so neither path
relies on a single mechanism.

## Verify (REAL bg job, not unit tests)
- dispatch a bg task, let it COMPLETE
- .snodo/jobs/<id>/state.json has non-empty usage[] with job_id ==
  the real j_ id, per-call tokens + cost
- snodo meta j_<id> shows real tokens + cost (not 0)

## Touch
infrastructure/usage_tracker.py (read SNODO_JOB_ID),
engine/loop.py:1072 (set _job_id on fresh_coder — optional belt)

Commit: fix(usage): read SNODO_JOB_ID env var for job correlation, fix bg token capture
