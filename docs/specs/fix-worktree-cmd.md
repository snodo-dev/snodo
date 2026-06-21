# Spec: fix malformed git worktree add command

## Bug (now visible via the logged error)
create_worktree builds:
  git worktree add <path> task/{id}/{slug} main   -> exit 129 (usage error)
Missing the -b flag. git reads branch + base as bare positional args.

Correct:
  git worktree add <path> -b task/{id}/{slug} main

## Also: double task_ prefix
Path is .snodo-worktrees/task_task_c981a3 — id is already "task_c981a3"
but code prepends another "task_". Fix the path construction to not
double-prefix (use the id as-is, or strip/don't add the extra task_).

## Fix
infrastructure/worktree.py create_worktree — the git worktree add call:
- add -b before the branch name:
  repo.git.worktree("add", str(worktree_path), "-b", branch, base)
  (confirm the gitpython arg form; -b must precede branch)
- fix worktree_path to use task_id directly without double "task_"

## Verify (real parallel dispatch)
- two parallel bg tasks -> git worktree list shows TWO siblings at
  ~/Dev/.snodo-worktrees/<task_id>/ (single task_ prefix), both off main
- main repo stays on main
- no "Worktree creation failed" in stderr
- branches created IN worktrees not main

## Touch
infrastructure/worktree.py only

Commit: fix(isolation): add missing -b flag and fix double task_ prefix in worktree add
