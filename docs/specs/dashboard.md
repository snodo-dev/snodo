# Dashboard rebuild — k9s-style session monitor

## Goal
Replace `snodo dashboard` with a keyboard-first, live, k9s-style TUI. Drop the old jobs/plans flat-panel layout entirely. Read-only over existing state plus two contextual actions that use existing backend. NO engine/data-model changes — present the model, don't reshape it to fit the UI.

## Domain model (the spine)
workspace (folder) -> sessions (one is active) -> code agents + validators -> events.

## Shared read-layer
Add a UI-agnostic read-layer (e.g. DashboardDataProvider) that aggregates the existing managers into this tree. The TUI consumes it. Keep it free of Textual imports so a future `snodo dashboard --web` can reuse it unchanged (web is a later, separate task — just don't block it).

## Data sources (use what exists; ground in these, don't invent)
- Sessions list: SessionManager.list_sessions(); active session: ProjectState.active_session (.snodo/state.json). No "running" concept — list what exists, active pinned on top.
- Session mode: SessionState.mode. Validators: protocol.get_mode(mode).validators (protocol from .snodo/protocol.yml). Agents: AgentMemoryManager, keyed by project:mode.
- Agent model (show): resolve from existing config (ConfigManager.get_model / Mode.coder_config / coder). Change: via the existing config setter only — applies on next run, no new per-agent persistence.
- Events: AuditLog (.snodo/audit.log, NDJSON, hash-chained). Read incrementally (track file position) — read optimization only; do NOT change the audit format or hash chain.

## TUI (k9s patterns)
- Primary view: sessions as a live resource table — session, mode, #agents, #validators, last event, active marker. Active pinned top + highlighted. Color by status.
- Drill in (Enter) -> session detail: mode, validators (verdict state, colored), agents (each with its model), and that session's recent events. Esc pops back. Breadcrumb header: workspace > session.
- `:` command bar to switch views: :sessions, :agents, :events.
- `/` filters the current list.
- Live, in-place updates: update rows in place, never table.clear()+re-add — preserve cursor/selection across refresh. This is the fix for the old selection-reset bug; do it by construction.
- Header block: workspace path, active session, counts. Contextual footer showing keys valid in the current view.

## Contextual actions (existing backend only)
- On an agent: `m` -> change model via the existing config setter (state clearly it applies next run).
- On an escalated/halted session: surface it prominently (color) and offer the existing resolve flow (resolve_disagreement / apply_resolution) in place.

## Out of scope (now)
- (c) codebase/open PRs/branches/changed files — marginal; defer (later: lazy-load for the selected session via GitHubProvider).
- `--web` — later task; only constraint now is the read-layer stays UI-agnostic.
- Any engine/data-model change (per-agent model persistence, session running-state, audit format) — explicitly NOT in scope.

## Rules
Ground every read in the actual managers (recon doc has file:line). Build on Textual's native features (DataTable in-place update, command palette, screen stack, dynamic Footer bindings, CSS) — don't hand-roll. Read before writing.
