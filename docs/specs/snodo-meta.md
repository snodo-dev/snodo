# Spec: snodo meta — compact task/job summary

## Why
Orchestrator (and human) needs a cheap summary surface: poll meta
instead of full status/logs. If success -> move on; if not -> pull
detail. Saves tokens. Shows timing, status, tokens, cost, and a
one-line highlight.

## Two prerequisites + the command

### Part 1 — persist the halt payload to state.json
Today the structured halt payload (final_decision, validator_results,
phase, blocker reason) is printed/in-memory only. Persist it.

- Where the run reaches a terminal outcome (the loop's halt/complete
  path that currently builds the printed STRUCTURED HALT PAYLOAD),
  write it into the job's state.json under a "halt" key.
- Capture what's already computed — no new computation:
    halt: {
      final_decision, phase,
      pre_validation:  {policy_decision, validator_results[], outcome},
      post_validation: {…} | null,
      blocker_reason (if any),
    }
- Write via the existing job-state update path (jobs/__init__ /
  usage_tracker use _save_state-style append/merge). Merge, don't
  clobber usage[] or status.

### Part 2 — snodo meta command (new cli/commands/meta_cmd.py)
  snodo meta <id>
- id starts j_  -> single job
- id starts task_ -> aggregate across all jobs for that task

Single job (j_xxx):
  read .snodo/jobs/<id>/state.json + task.json. Emit:
    status, started_at/completed_at -> duration,
    tokens (sum usage[].total_tokens, split prompt/completion),
    cost (sum usage[].cost; show "partial" if any cost is null),
    per-role token breakdown (coder vs each validator) from usage[].role,
    highlight (synthesised — see below).

Task (task_xxx) — aggregate multiple jobs:
  scan .snodo/jobs/*/task.json, collect job dirs where task.id == id
  (linear scan, no reverse index — fine at current scale).
  For each matching job read state.json. Aggregate:
    - total tokens / cost summed across all jobs' usage[]
    - duration: earliest started_at -> latest completed_at
    - task outcome: latest job's halt.final_decision
    - per-job one-line highlight, listed
    - job count

Highlight synthesis (one line):
  - completed: "completed — N artifacts, {tokens} tok, ${cost}"
  - blocked:   "blocked at {phase}: {first blocker validator_id} — {short reason}"
  - failed:    "failed: {error/exit_code}"
  Pull blocker validator_id + justification from halt.validator_results.

## Output (table/compact, list_jobs style)
Single job:
  Job j_xxx  [completed]   12.4s
  Tokens: 84,200 (prompt 81k / completion 3.2k)   Cost: $0.012
  By role: coder 78k | validator:security 1.2k | validator:adr 4k ...
  Highlight: completed — 2 artifacts

Task aggregate:
  Task task_xxx   3 jobs   [blocked]   total 41.2s
  Tokens: 210k   Cost: $0.031
  Jobs:
    j_aaa completed  — 2 artifacts
    j_bbb blocked    — qa-unit tests failed
    j_ccc completed  — 1 artifact

## Pricing
Read pre-computed usage[].cost (set at capture via completion_cost,
dad5a3f register_model prices CF). If any record cost==null, mark the
total "partial (some calls unpriced)". Don't recompute.

## Register in main.py.

## Tests
- halt payload written to state.json on blocked AND completed runs
- meta j_xxx reads single job: status, duration, summed tokens, cost,
  per-role breakdown, highlight
- meta task_xxx scans + aggregates multiple jobs for the task
- cost shows "partial" when a usage record has null cost
- highlight: blocked -> names blocker validator + reason; completed ->
  artifact count
- task with one job == that job's numbers

## Touch
engine/loop.py (persist halt to state.json at terminal path),
jobs/__init__.py (state merge helper if needed),
cli/commands/meta_cmd.py (new), cli/main.py (register)

Commit: feat(meta): snodo meta — job + task-aggregate summary (timing, tokens, cost, highlight)
