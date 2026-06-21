# Wave 12 P1: Task retry mechanism

## Intent
When a task fails post-validation, the engineer can retry on the
same branch with the failure context automatically included in the
coder prompt. The coder sees what it wrote, why it failed, and fixes
specifically that. No human reformulation needed for mechanical
failures (test failures, style violations, etc).

## Depends on
Wave 12 P0 must be merged first — P1 assumes branches exist.

## What to build

### 1. Persist failure context on halt (engine/loop.py)
When a task halts (blocker or post-validator fail), write a
structured failure context to session.checkpoint.decisions:

  decisions["task_failure"][task_id] = {
      "spec": task.spec,
      "branch": branch_name,
      "attempt": attempt_number,
      "failed_validators": [
          {
              "validator_id": ...,
              "severity": ...,
              "justification": ...
          }
      ],
      "files_changed": [list of files from artifact],
      "timestamp": iso_timestamp
  }

This is separate from pending_decisions (adjudication) — it's
failure context for retry, not a governance record.

### 2. snodo run --retry task_a1b2c3 (cli/commands/run_cmd.py)
Add --retry flag:
  snodo run --retry <task_id> [optional revised spec]

On --retry:
  1. Resolve project root, load session
  2. Read decisions["task_failure"][task_id] from checkpoint
     If not found: "No failure context for task_id. Cannot retry."
  3. Clear pending_decisions[task_id] (stale from previous attempt)
  4. Checkout existing branch: git_mcp.checkout_branch(branch_name)
  5. Build augmented prompt:
       "Original spec: {original_spec}

        Previous attempt {attempt_number} failed post-validation:
        {failed_validator_id}: {justification}

        Files changed in previous attempt:
        {files_changed}

        Fix the issues above."
     If revised spec provided: replace original_spec with revised spec
  6. Run through protocol from governance (pre-validators run again)
  7. Increment attempt_number in failure context on next halt
  8. Clear failure context on success

### 3. Enforce max_retries (engine/loop.py)
Read protocol.execution.max_retries (default 3 from ExecutionConfig).
Track attempt_number in task failure context.
If attempt_number >= max_retries:
  escalate to human:
  "Task task_a1b2c3 has failed {max_retries} times.
   Review branch task/task_a1b2c3/... and either:
   - snodo run --retry task_a1b2c3 "revised spec" (override spec)
   - snodo task abandon task_a1b2c3 (delete branch)"
  Do NOT auto-retry. Human decides.

### 4. snodo task command (cli/commands/task_cmd.py, new)
snodo task list
  Lists all task branches in current project with status:
  TASK ID      BRANCH                              ATTEMPT  STATUS
  task_a1b2c3  task/task_a1b2c3/add-jsdoc-getcookie  2       failed
  task_b4d5e6  task/task_b4d5e6/fix-auth-cookie       1       ready

snodo task abandon <task_id>
  Deletes branch task/{task_id}/... (git branch -D)
  Clears failure context from checkpoint
  Prints: "Branch deleted. Task abandoned."

snodo task prune [--stale-days 7]
  Lists task branches with no commits in --stale-days days
  Prompts for confirmation
  Deletes stale branches (git branch -D)
  Useful for cleanup without manual abandon per task

Register under main.py as snodo task <subcommand>

## Acceptance criteria
- snodo run --retry task_a1b2c3 checks out existing branch
- Augmented prompt includes failure context + files changed
- Optional revised spec replaces original spec on retry
- attempt_number increments per retry
- At max_retries: escalate message, no auto-retry
- pending_decisions cleared for task_id on retry start
- snodo task list shows all task branches with status
- snodo task abandon deletes branch + clears context
- snodo task prune deletes stale branches with confirmation
- Full suite passes

## Constraints
- Read cli/commands/run_cmd.py, engine/loop.py (halt path,
  _auto_write_pending_decisions), mcp/git.py (checkout_branch
  from P0, delete_branch), compiler/models.py (ExecutionConfig
  max_retries from P0), infrastructure/session.py (checkpoint
  structure) before touching anything
- P0 must be merged before this ticket
- Failure context is NOT a governance record — it is operational
  state for retry. Do not use the RS256 signing path.
- max_retries escalation is informational only — no auto-action
- snodo task prune requires explicit confirmation — never silent delete
