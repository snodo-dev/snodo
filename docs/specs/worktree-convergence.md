# Spec: worktree setup at JobManager.submit (covers CLI + background)

## Root cause
Worktree creation lives in _execute_task (execution phase). Works for
CLI inline (in-process). FAILS for background: JobManager.submit
spawns the subprocess with cwd ALREADY set to project_root before
_execute_task runs — so the subprocess works in the main repo, no
isolation. Timing mismatch: worktree must be set up BEFORE spawn for
the background path.

Both paths DO reach _execute_task (background re-enters via
wrapper.py -> cli.main), but the background spawn commits cwd too early.

## Fix: move worktree setup upstream to a shared setup point, BEFORE spawn

### 1. JobManager.submit creates the worktree (before spawn)
jobs/__init__.py:submit — before spawn_background:
  - compute task_branch_name + worktree_path
  - create_worktree(worktree_path, branch, base=main)
  - set cwd = worktree_path (not project_root) for the spawned process
  - pass worktree_path in task_args so the job knows its isolated root
Background path now spawns INTO the worktree.

### 2. _execute_task stops creating worktrees when one already exists
If task_args/env already carries a worktree_path (set by JobManager),
_execute_task uses it, does NOT create its own. Avoids double-creation.

### 3. CLI inline path also routes worktree setup through the same helper
CLI inline doesn't go through JobManager.submit. Extract worktree
setup into a shared helper (e.g. worktree.setup_for_task) that BOTH
JobManager.submit AND the CLI inline path call. One setup logic, two
callers. CLI inline creates its worktree via the same helper before
running the graph in-process.

### 4. Cleanup unchanged
finally-block removal (CLI) + task abandon/prune hooks already exist.
Confirm background path also removes its worktree on completion (in
wrapper.py finally or JobManager, since CLI finally won't run for the
detached subprocess's worktree).

## Verify (the test that actually matters)
- dispatch a BACKGROUND/MCP task -> ~/Dev/.snodo-worktrees/task_{id}/
  is created, git worktree list shows a SIBLING worktree (not main
  repo on a task branch)
- two parallel background tasks -> two separate worktrees, both off main
- CLI inline task -> also gets a worktree via the shared helper
- worktree removed after completion on BOTH paths

## Touch
jobs/__init__.py (submit: worktree before spawn, cwd=worktree),
jobs/runner.py (if cwd plumbing needs it),
infrastructure/worktree.py (shared setup_for_task helper),
cli/commands/run_cmd.py (_execute_task: use existing worktree, route
CLI inline through shared helper),
jobs/wrapper.py (background cleanup on completion)

Commit: fix(isolation): worktree setup before spawn in JobManager — covers CLI + background
