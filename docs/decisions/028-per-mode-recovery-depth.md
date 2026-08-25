# ADR 028 — Per-mode max_recovery_depth override

## Status
Accepted

## Context
`execution.max_recovery_depth` was introduced to cap recursive subtask recovery along a single branch (default `3`). However, greenfield protocols contain modes that differ fundamentally in kind:
- `decide` and `scaffold` are setup and bootstrap phases where early validator rejections are environment or config faults (unrecorded ADRs, placeholder test commands like `REPLACE_ME`) that subtasks cannot fix.
- `build` is a feature development phase where the codebase and test harness are verified, and multi-step recovery (depth 3) enables incremental subtask fixes.

A single protocol-level depth cap forces a single answer across modes that differ in kind.

## Decision
Introduce `mode.max_recovery_depth` (default `null`), allowing a mode to override `execution.max_recovery_depth`.

Resolution follows the shared pattern established by `auto_merge` (ADR 018):
- **Shared Resolution**: `Protocol.resolve_mode_setting(mode_id, field_name)` resolves setting overrides.
- **Protocol Method**: `Protocol.max_recovery_depth_for(mode_id)` checks `mode.max_recovery_depth`; if `null`, it falls back to `protocol.execution.max_recovery_depth`.
- **Silent Modes**: When a mode is silent (`null`), it inherits the protocol-level setting (`execution.max_recovery_depth`, which defaults to `3`).

## Invariants & Execution Stability
- **Single-Mode Execution Loop**: The engine executes a single mode per invocation (`loop_state.current_mode`).
- **Mid-Recovery Mode Stability**: Automated recovery subtasks (`_fix_1`, `_fix_2`) derive from the root task and execute strictly within `loop_state.current_mode`. Cross-mode transitions (e.g. `decide` -> `scaffold`) are explicit user/CLI operations (`snodo mode change`), not automated steps inside a recovery loop. Therefore, a task **cannot change mode mid-recovery**, and per-mode depth resolution is completely unambiguous throughout recovery.
