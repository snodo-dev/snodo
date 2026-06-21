# Walk-up project root resolution + nested-init guard

## Intent
snodo commands resolve project root via Path.cwd() directly (23 call
sites, 12 files) — no git-like walk-up. Running from a subfolder either
fails (commands that check .snodo) or silently creates a DIFFERENT project
identity (project_id = sha256(cwd), so a subfolder hashes differently than
root). And snodo init only checks cwd, so it can create a nested .snodo
inside an existing project, corrupting the structure. Fix both: a single
walk-up resolver all commands use, and an init guard that refuses nesting.

## What to build

### infrastructure/paths.py — resolve_project_root
Add resolve_project_root(start: Optional[str] = None) -> Optional[str]
(sibling to resolve_home, same module):
- Start at start or Path.cwd()
- Walk up parent directories looking for a .snodo/ directory
- Stop at filesystem root
- Return the directory CONTAINING .snodo (the project root), or None if
  none found up the tree
- Keep it dependency-light — paths.py must stay cheap to import (it's
  used by the prompt command; no pydantic/engine imports)

A companion that callers use for "I need a project root or it's an error":
decide whether resolve_project_root returning None should raise a clear
error or fall back to cwd-with-warning. Recommend: a helper
require_project_root() that raises a clear "not inside a snodo project
(no .snodo found in this or any parent directory)" error, used by commands
that require a project. resolve_project_root (nullable) stays for callers
that tolerate absence (prompt command).

### Replace cwd usage at the 23 call sites
Replace Path.cwd()-as-project-root with resolve_project_root() /
require_project_root() at the command call sites:
  authorize_cmd, session_cmd, mode_cmd, run_cmd, plan_cmd, job_cmd,
  install_cmd, dashboard_cmd, sandbox_cmd, plan_run, dashboard/app,
  mcp/decision_handlers
- Engine/validator working_directory fallbacks (loop.py 691-692/1162,
  validators.py 90-91, quality.py 46) are a DIFFERENT concern (cwd for
  execution, not project identity) — leave those unless they're clearly
  project-root. Confirm per-site: is this cwd used as project_root
  (replace) or as a working dir for a subprocess (leave)?
- Relative .snodo references (mode_cmd Path(".snodo/protocol.yml"),
  audit.py default) should resolve relative to the project root, not cwd.

### init_cmd.py — nested-init guard
Before creating .snodo: walk UP for an existing .snodo in any parent
(reuse resolve_project_root). If a parent already has one, refuse with a
clear error ("already inside a snodo project rooted at <path>; nested
.snodo is not allowed") unless --force. Keep the existing cwd .snodo
check too.

## Acceptance criteria
- resolve_project_root walks up, finds .snodo in a parent, returns the
  containing dir; returns None at fs root with no .snodo
- Running a command from a subfolder resolves to the SAME project_root
  (and thus same project_id) as running from root
- require_project_root raises a clear error outside any snodo project
- snodo init refuses to create a nested .snodo when a parent has one
  (unless --force)
- Relative .snodo references resolve against project root, not cwd
- paths.py stays import-cheap (no pydantic/engine imports added)
- Commands that legitimately use cwd for subprocess working-dir are
  unchanged (only project-identity cwd usage is replaced)

## Testing
- Unit: resolve_project_root from nested subfolder finds root .snodo
- Unit: resolve_project_root returns None when no .snodo up the tree
- Unit: project_id identical from root and from subfolder (the bug fix)
- Unit: require_project_root raises clear error outside a project
- Unit: init refuses nested .snodo (parent has one), allows with --force
- Unit: init still works normally in a fresh dir
- Regression: existing commands still work when run AT project root
- Full suite passes (SNODO_HOME isolation already in place; note tests
  may need a project-root fixture — use temp dirs, not /Users/test)

## Constraints
- Read infrastructure/paths.py (resolve_home pattern), infrastructure/
  session.py (_project_id), init_cmd.py, and the command call sites before
  touching anything
- paths.py must remain dependency-light — the prompt command imports it
- Distinguish project-identity cwd (replace) from subprocess-working-dir
  cwd (leave) per call site — do not blindly replace all 23
- This is the foundation for the prompt command (separate ticket)
