# W5-00: Authoritative active session per (project, mode)

## Intent
state.json:active_session is read-never-written — a dead placeholder.
Session resolution falls back to first-match glob, which is
non-deterministic with multiple sessions per (project, mode). Wire the
authoritative active pointer: exactly one active session per (project,
mode), persisted on disk. This is the prerequisite for HI-CTRL
authorization (decisions are scoped to the active session).

Model:
- Many sessions can exist per (project, mode) — each is a context boundary
- Exactly ONE is marked active on disk per (project, mode)
- On entering a mode / starting work: if no sessions exist, create one
  and set active; if sessions exist, act on the active one
- User can create a new session (set active) or switch to an existing one
- Always exactly one active — nothing runs/loads unless you're working
  on the active session

Mode separation stays at the MCP boundary (producer MCP vs reviewer MCP) —
NOT enforced in code. This ticket only fixes active-session resolution
WITHIN a (project, mode).

## What to change

### state.json — active pointer becomes per-mode
active_session becomes a dict keyed by mode:
  active_session: {"producer": "sess_x", "reviewer": "sess_y"}
(or None entries). A single string is insufficient — per-mode is the model.
Update ProjectState model accordingly. Migrate old state.json (active_session: null)
cleanly — treat as empty dict.

### session.py — write the pointer
- create_session: after creating, set it active for its (project, mode)
  in state.json
- Add set_active_session(project_root, mode, session_id) — writes the
  pointer, validates the session exists and matches (project, mode)
- get_active_session(mode, project_root): READ the pointer from state.json
  first. If pointer set and session exists → return it. If pointer unset
  or stale → fall back to: if exactly one session for (mode, project)
  exists, adopt it as active and write the pointer; if none, return None
  (caller creates); if multiple and no pointer, this is the ambiguous
  case — pick most-recently-updated, set it active, log a warning.
- Replace the first-match glob as the primary path — pointer is authoritative.

### snodo session CLI — add lifecycle actions
- snodo session switch <session_id> — set an existing session active for
  its (project, mode)
- snodo session new — create a new session and set it active
- (list/show/delete/prune already exist — keep)
- delete: if deleting the active session, clear or reassign the pointer

### mode_cmd.py
- snodo mode change updates current_mode AND ensures the target mode has
  an active session pointer (resolve or create)

### Fix the orphaned readers
- dashboard providers/screens: is_active now actually works via the pointer
- prune_stale: do NOT prune the active session (the docstring claimed this
  but it was never true — make it true)

## Acceptance criteria
- active_session is written on session create and on switch/new
- get_active_session reads the authoritative pointer, not first-match
- Exactly one active session per (project, mode), deterministic
- snodo session switch / new work
- Deleting the active session clears/reassigns the pointer
- prune_stale never prunes the active session
- Dashboard correctly pins the active session
- Old state.json (active_session: null) migrates cleanly
- Mode separation unchanged — still MCP-boundary, not code-enforced

## Testing
- Unit test: create_session sets active pointer
- Unit test: get_active_session reads pointer, not glob
- Unit test: multiple sessions + pointer → returns pointed-to one
- Unit test: multiple sessions, no pointer → adopts most-recent, writes pointer
- Unit test: switch sets active, get returns the switched-to session
- Unit test: delete active → pointer cleared/reassigned
- Unit test: prune never removes active
- Unit test: per-mode pointers independent (producer + reviewer)
- Unit test: old state.json migrates
- Full suite passes clean

## Constraints
- Read session.py (SessionState, get_active_session, create_session,
  prune_stale), state.py (ProjectState, read/write_state), session_cmd.py,
  mode_cmd.py, dashboard providers/screens before touching anything
- Mode separation is NOT enforced in code — do not add cross-mode guards
- This is the prerequisite for HI-CTRL (W5-05) — get the active pointer
  authoritative and deterministic
