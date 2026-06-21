# snodo-prompt: lightweight shell-prompt status command

## Intent
A fast status command for a shell prompt segment (like git showing the
branch). Shows the current snodo project's mode + active session when
inside a project, nothing otherwise. MUST be fast — it runs on every
shell redraw. The full snodo CLI imports the compiler/pydantic/langchain
chain (~1813ms); state.py + paths.py alone import in ~13ms. So this is a
SEPARATE lightweight entry point, NOT a subcommand of the heavy CLI.

Depends on resolve_project_root (walk-up resolver ticket).

## What to build

### A standalone lightweight entry point
A separate console script (e.g. `snodo-prompt` in pyproject.toml
[project.scripts]) whose module imports ONLY:
  - infrastructure/paths.py (resolve_project_root)
  - infrastructure/state.py (read_state)
and NOTHING from compiler/, engine/, cli/commands, or langchain. Verify
the import cost stays well under ~50ms.

Behavior:
- resolve_project_root() — walk up for .snodo
- If None (not in a project): print nothing, exit 0
- Else: read_state(project_root) → current_mode + active_session dict
- Print a short string: the current_mode and the active session id for
  that mode (e.g. "producer:sess_..." or just the mode if no active
  session). Keep the format minimal and stable — it's consumed by a
  shell prompt.
- Must never error loudly into the prompt — on any failure (no state,
  unreadable), print nothing and exit 0. A prompt segment must degrade
  silently.

### Output format
Decide a clean minimal format. Suggested: print just `mode` or
`mode:short_session` (short_session = last 6 chars of session id). No
ANSI/color — the shell theme adds styling. No newline issues — single
line, no trailing noise.

## Acceptance criteria
- A separate entry point (not under the heavy `snodo` CLI) — importing
  only paths + state
- Import + execution well under 50ms (measure; the heavy CLI's 1813ms is
  the thing being avoided)
- Inside a project: prints mode (+ active session) for the current mode
- Outside a project: prints nothing, exits 0
- Never raises into the prompt — silent degradation on any error
- No langchain/pydantic/compiler import (verify the import graph)

## Testing
- Unit: inside a project with state → correct mode/session string
- Unit: outside a project → empty output, exit 0
- Unit: corrupt/missing state.json → empty output, exit 0 (no raise)
- Test/assertion: the prompt module's imports do NOT include compiler,
  engine, cli.commands, or langchain (import-graph guard, like the
  signing-boundary test)
- Measure import time stays under threshold

## Constraints
- Read infrastructure/paths.py, infrastructure/state.py, pyproject.toml
  [project.scripts], and main.py (to see what the heavy import chain pulls
  — and avoid all of it) before touching
- ONLY import paths + state — an import-graph test must enforce this so a
  future import doesn't silently make it slow again (same pattern as the
  signing import-boundary guard)
- Silent degradation — never error into the prompt
- Depends on resolve_project_root from the walk-up resolver ticket — land
  that first
