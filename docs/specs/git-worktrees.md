# Spec: git worktree isolation for parallel task execution

## Why
Parallel tasks share the same working directory. Job A's git ops
(checkout, branch creation) change filesystem state that jobs B and C
are simultaneously reading/writing — race conditions, chained branches,
non-isolated work. git worktrees give each task its own working dir
backed by the same .git — true parallel isolation, git-native.

## Worktree lifecycle

### Creation (before graph execution)
In build_protocol_graph (engine/loop.py:1511) or its caller, before
initializing MCPs:
  git worktree add <worktree_path> -b task/{id}/{slug} main
  worktree_path = <main_project_root>/../.snodo-worktrees/task_{id}/
  (sibling to repo, not inside it — avoids accidental git tracking)

Branch always off main (not HEAD) — fixes the chaining bug.

### MCP initialization — use worktree_path, NOT project_root
  workspace_mcp = WorkspaceMCP(worktree_path)
  git_mcp = GitMCP(worktree_path)
  shell_mcp = ShellMCP(worktree_path)
All file reads/writes, git ops, test runs go to the worktree.

EXCEPTION: _merge_into_job_state MUST use main project_root (not
worktree) — job state must persist past worktree removal. Keep this
path explicitly pointing to the main repo's .snodo/jobs/.

### Merge (unchanged from user's perspective)
From the main repo (not the worktree):
  git merge --squash task/{id}/{slug}
  git commit -m "..."
  git branch -D task/{id}/{slug}
  git worktree remove <worktree_path>
The squash-merge flow the user already runs stays identical.

### Cleanup
- On merge: git worktree remove <worktree_path> (safe post-merge,
  no branch deletion required first)
- On job prune/abandon: git worktree remove <worktree_path> || rm -rf
  (worktrees persist if job fails — must be explicitly cleaned up)
- Hook into snodo job prune + snodo task abandon: remove orphaned
  worktree dirs alongside branch/job cleanup
- .snodo-worktrees/ gitignored in main repo

## snodo init bug (track together)
While touching init: fix path-traversal check in
cli/commands/init_cmd.py:
- Skip ~/.snodo when walking up for existing snodo init (it's global
  config, not a project)
- Hard-block if resolved project root would be ~/ (home dir init)

## Tests
- parallel dispatch: two tasks each get their own worktree dir,
  both branch off main (not each other)
- job A's git ops don't affect job B's working dir
- merge from main repo works after worktree-based task completes
- job state.json written to main repo .snodo/jobs/ (not worktree)
- failed/abandoned job: worktree cleaned up by prune/abandon
- worktree dir absent from git status in main repo

## Touch
engine/loop.py (build_protocol_graph — worktree creation, MCP init
with worktree_path, keep _merge_into_job_state on main root),
mcp/git.py + mcp/workspace.py + mcp/shell.py (accept path at init,
no hardcoded assumptions),
cli/commands/init_cmd.py (snodo init bug),
snodo job prune + task abandon (worktree cleanup),
.gitignore (add .snodo-worktrees/)

Commit: feat(isolation): git worktree per task for parallel execution isolation
