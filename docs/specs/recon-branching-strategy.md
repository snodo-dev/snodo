# Recon: branching strategy + task iteration model

## Proposed design — validate, find issues, suggest improvements

### Core model
Every snodo task executes on an isolated git branch. Main is never
touched directly by the engine. The human controls what merges to main
via PR.

### Branch lifecycle

1. PRE-VALIDATION (no branch yet)
   - pre-validators run against the spec
   - if blocked: halt, no branch created, nothing to clean up
   - if pass: CREATE branch task/{task_id}/{slug}
     slug = first 5 words of spec, slugified
     e.g. task/task-82eb82/add-jsdoc-getcookie-function

2. EXECUTION (on task branch)
   - coder executes on the task branch
   - opencode commits on the branch (snodo does NOT double-commit)
   - post-validators run

3. POST-VALIDATION OUTCOMES
   a. ALL PASS → branch is PR-ready. Engine prints:
      "Task complete. Branch: task/task-82eb82/add-jsdoc-getcookie
       Raise PR when ready."
      Human raises PR, reviews, merges to main.

   b. FAIL → branch exists with the attempt's commits.
      Engine records failure context (validator ID, justification,
      severity) in the task's audit record.
      Engine prints:
      "Task failed post-validation. Branch preserved:
       task/task-82eb82/add-jsdoc-getcookie
       Retry with: snodo run --retry task-82eb82"

4. RETRY (snodo run --retry task-82eb82)
   - Checkout existing branch task/task-82eb82/...
   - Build augmented prompt:
       Original spec + previous attempt failure context
       (validator ID, justification, what files were changed)
   - Coder sees current branch state + failure reason
   - Coder fixes specifically what failed
   - Post-validators run again
   - Max retries from protocol.yml (default: 3)
   - After max retries: escalate to human

5. ABANDON (snodo task abandon task-82eb82)
   - Deletes branch task/task-82eb82/...
   - Marks task as abandoned in audit log
   - Human explicit action — never automatic

6. OPTIONAL SPEC REVISION ON RETRY
   snodo run --retry task-82eb82 "revised spec"
   - Same branch, new spec replaces original for this iteration
   - Failure context still included in coder prompt
   - Useful when original spec was wrong, not just implementation

### Branch naming
task/{task_id}/{slug}
- task/ prefix always (future: feat/, bug/, security/ when protocol
  has typed tasks)
- task_id always in format task-{6hex}
- slug: auto-generated from spec, max 5 words, lowercase-hyphenated

### Iteration model
- Branch accumulates commits across retries (full iteration history)
- PR shows the journey: attempt 1, fix, attempt 2, etc.
- Human can squash before merging
- Each retry is a new commit on the same branch, not a new branch

### Protocol config
execution:
  max_retries: 3  # escalate to human after N failed post-validations

### Multi-task isolation
- Each task gets its own branch from main
- Parallel tasks are independent branches
- Dependencies managed by human merge order (V1)
- Branch stacking (task B branches from task A) is future work

### OpenCode + git
- snodo creates branch BEFORE dispatching to OpenCode
- OpenCode commits on the task branch (its natural behavior)
- snodo does NOT double-commit (skip engine git commit for OpenCodeAdapter)
- Post-validators run after OpenCode's commit
- snodo owns branch creation/deletion, OpenCode owns commits on branch

### snodo init git requirement
- snodo init must verify .git exists (walk up like resolve_project_root)
- Fail loudly if not in a git repo: "snodo requires git. Run git init."

### Implementation touchpoints
- engine/loop.py: branch creation before execute, branch checkout on retry
- cli/commands/run_cmd.py: --retry flag, task ID lookup
- mcp/tools.py: dispatch_task gets optional task_id for retry
- mcp/server.py: handle retry dispatch
- cli/commands/task_cmd.py (new): snodo task abandon/list/status
- coders/opencode_adapter.py: skip_engine_commit flag
- protocol.yml schema: execution.max_retries

## Questions for the agent

1. BRANCH CREATION TIMING
   We create the branch after pre-validators pass. But the coder
   (OpenCode) needs to be on the branch before it starts writing.
   How does the engine ensure OpenCode starts on the correct branch?
   Is it enough to checkout the branch before starting the container,
   or does OpenCode need to be told explicitly which branch to use?

2. RETRY CONTEXT BUILDING
   When --retry is called, the engine needs to find the previous
   attempt's failure context (validator results, what files changed).
   Where is this stored today? Is it in the audit log, the checkpoint,
   or nowhere? Can it be reliably retrieved for a task by ID?

3. CONCURRENT TASKS
   If two tasks are dispatched simultaneously (recon + implementation),
   each gets its own branch. But snodo currently runs one task at a
   time per session. Is there a concurrency risk where branch checkout
   races between tasks? Or is this a non-issue given the single-task
   execution model?

4. OPENCODE + BRANCH
   OpenCode detects the .git directory and commits automatically.
   If snodo checks out branch task/task-82eb82/... before starting
   the OpenCode container, will OpenCode commit on that branch?
   Or does OpenCode always commit on whatever HEAD is at container
   start? Any risk of OpenCode switching branches itself?

5. POST-VALIDATOR FAILURE + PARTIAL COMMITS
   If OpenCode makes 3 commits during a task (it may commit
   incrementally) and post-validators fail, the branch has 3 commits
   of bad code. On retry, the coder sees all 3 commits as context.
   Is that useful or confusing? Should the branch be reset to the
   pre-task state before retry, or should all attempts accumulate?

6. MERGE CONFLICTS
   Task A and Task B both branch from main at the same point. Task A
   modifies auth/cookie.ts. Task B also modifies auth/cookie.ts.
   Task A merges first. Task B now has a conflict. How does snodo
   surface this? Does it detect the conflict before the PR, or only
   when the human tries to merge?

7. SCALABILITY CONCERNS
   At 100 tasks/day, how many branches accumulate? If engineers
   don't raise PRs promptly, the repo fills with task branches.
   Is the abandon mechanism enough, or do we need automatic TTL
   on stale branches (e.g. 7 days without activity)?

8. BLIND SPOTS / SUGGESTIONS
   What aspects of this design don't scale, create unexpected
   coupling, or conflict with existing snodo mechanisms (HI-CTRL,
   audit trail, cloud sync, recon)? What would you change?

Use Qwen (ollama-gx10/qwen3-coder-next:latest at 192.168.0.106)
for this recon — we want a fresh perspective from a coding-focused
model. Read the existing codebase (engine/loop.py, mcp/git.py,
coders/opencode_adapter.py, cli/commands/run_cmd.py) before
answering. Empirical answers preferred over speculation.
