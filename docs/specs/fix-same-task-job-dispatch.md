# FIX: dispatch a new tracked job to the same task (background-only)

## Goal
Persist task_id at dispatch and make `snodo job retry` perform a true same-task
retry. The previous attempt broke this by loading a task from a job dir inside
the SHARED inline execution path, which killed every non-background run.

## Scope
The background dispatch path and the `job retry` path only.

## HARD do-not-touch
- The shared inline _execute_task path must NOT require a job dir and must NOT
  call _load_task(_job_dir(job_id)) unconditionally. Inline / non-background runs
  (--from-pr, --sandbox local, docker fallback, plain `snodo run`) have no job
  dir and must execute exactly as before this change.
- Any load of a stored task from a job dir must be guarded: only when running as
  a real background job (job_id present AND the job dir exists). Otherwise skip
  silently and derive task_id as today.

## Contracts
1. At background dispatch, persist task_id into the job record (task.json).
2. `snodo job retry <job_id>` resolves task_id from the stored value and runs a
   true same-task retry: same task_id, prior failure context, as a NEW tracked
   job. New-task fallback only when no stored task_id exists (legacy jobs).
3. Inline runs are behaviorally unchanged.

## Acceptance
- The four previously-broken inline tests (test_from_pr x2, sandbox local,
  docker fallback) pass — no JobError from a missing job dir.
- A backgrounded job's task.json contains its task_id.
- `job retry` yields a new j_xxx with the SAME task_id, grouped under the same
  task in `snodo meta <task_id>`, carrying prior failure context.
- Full suite green.

## Note
The leaked SNODO_JOB_ID (test_argv_passed_to_cli0) across tests is a test-
isolation gap; the guard above makes it harmless. Isolating SNODO_JOB_ID in the
autouse fixture (like SNODO_HOME) is cheap hygiene if convenient — not required.
