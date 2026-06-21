# Spec: surface worktree errors + make engine branch creation worktree-aware

## Two compounding bugs
1. setup_for_task swallows all exceptions silently (worktree.py:97-100,
   `except Exception: return None`, no log) — we can't see WHY worktree
   creation fails.
2. The engine creates the task branch in the MAIN repo regardless of
   worktree (loop.py:1271 git_mcp.create_branch, git_mcp tied to main
   project_root). So even a successful worktree is defeated — the engine
   checks out the branch in the main working dir anyway.

## Fix

### 1. Stop swallowing the error (do this FIRST — diagnostic)
worktree.py setup_for_task: log the exception (logger.warning with the
actual GitCommandError/message) before returning None. We need to SEE
the real failure, not guess. Do not silently degrade without a logged
reason.

### 2. Make the engine worktree-aware
The core bug: when a worktree IS active, the engine must NOT create the
branch in the main repo — the worktree is ALREADY on the correct branch
(git worktree add -b created it). loop.py ~1267-1271:
  - if worktree_path is set: the branch already exists and is checked
    out IN the worktree. The engine's git_mcp (pointed at worktree_path
    when worktree active — verify mcp_root routing) must operate in the
    worktree, and must NOT re-create/checkout the branch in main.
  - Confirm git_mcp is initialized with worktree_path (not project_root)
    when a worktree is active (the build_graph mcp_root logic). If
    git_mcp still points at main repo, that's the bug — it must point
    at the worktree so create_branch/checkout/commit all happen there.

The earlier worktree commit claimed "_default_executor skips branch
creation when worktree active" — VERIFY that skip actually fires.
loop.py:1271 still calling create_branch suggests it doesn't.

### 3. Decide fallback behavior when worktree genuinely fails
If worktree creation fails (logged now), what should happen?
  - Option: hard-fail the task (no silent main-repo execution) — safer,
    no surprise branches in main.
  - Option: explicit "main-repo mode" with a loud warning.
Recommend hard-fail OR loud warning — never silent main-repo branch
creation (the current behavior that caused this).

## Verify (real dispatch, not unit tests)
- dispatch a background task -> SEE worktree created at
  ~/Dev/.snodo-worktrees/task_{id}/, git worktree list shows sibling
- the task branch is created IN the worktree, NOT in main repo
- main repo stays on main throughout (shell prompt stays on main)
- if worktree creation fails -> error is LOGGED (not swallowed) and
  task either hard-fails or loudly warns
- two parallel tasks -> two sibling worktrees, main untouched

## Touch
infrastructure/worktree.py (log the exception),
engine/loop.py (worktree-aware branch handling — don't create branch
in main when worktree active; confirm git_mcp points at worktree),
cli/commands/run_cmd.py + build_protocol_graph (verify mcp_root routes
git_mcp to worktree path)

Commit: fix(isolation): surface worktree errors + make engine branch creation worktree-aware
