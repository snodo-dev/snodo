# ADR 017 — WF1 relaxed to exclusivity on approval-conferring tools

## Status
Accepted

## Context
`check_wf1` enforced **total tool disjointness**: it refused to load any protocol
in which two modes shared *any* tool. That is stronger than the property WF1
exists to guarantee. WF1's purpose is to make **self-approval** impossible — an
actor in one mode must not be able to authorise its own work via the tools of
another mode. Total disjointness was one way to get there, and it had a useful
side effect: because every tool belonged to exactly one mode, the mode could be
inferred from the tool call alone.

That inference mattered for attribution. With disjointness relaxed, "which mode
performed this operation" can no longer be reconstructed from the tool name
alone, so attribution must move somewhere else (the audit log) rather than be
dropped.

Total disjointness is also what blocked legitimate protocols in which two
sequential stages both need `edit` (e.g. a greenfield plan/implement flow), even
though those stages never both hold an approval tool.

## Decision
WF1 checks **exclusivity of approval-conferring tools**, not total disjointness:

- `Protocol` gains an `exclusive_tools` set, defaulting to `{"approve", "merge"}`.
  A protocol may **extend** it, but may **not shrink** it — the defaults are
  always enforced by a model validator (`validate_exclusive_tools` unions the
  declared set with the defaults). Dropping an approval tool from the set would
  silently weaken the no-self-approval guarantee, so shrinking is a no-op rather
  than an error.
- `check_wf1` fails only when an exclusive tool is held by more than one mode,
  naming the tool and every holding mode. Non-exclusive tools may appear in any
  number of modes.
- Mode attribution moves to the audit log: every `tool_call` (and `wf1_violation`
  / `dispatch_request`) entry records the active mode via a new `_active_mode()`
  resolver on the MCP server — the pinned `mode_id` when the server is
  single-mode, otherwise `state.json`'s `current_mode`, falling back to the
  protocol's initial mode. It remains possible to determine, from the audit log
  alone, which mode performed a given operation.

## Why the weaker condition is sufficient

- **No self-approval.** The only operation that lets an actor bless its own work
  is the set of approval-conferring tools (`approve`, `merge`). Those are exactly
  the tools that remain exclusive to a single mode, so a protocol cannot grant
  them to two modes and cannot remove them from the exclusive set. An actor
  confined to one mode therefore cannot hold both sides of an approval.
- **Capability remains bounded by the active mode (INV2).** INV2 filters tool
  exposure to the mode the server is serving; it never depended on disjointness.
  Relaxing WF1 does not widen what any single mode can do — a mode's capability
  is its declared `tools` list, unchanged.
- **Attribution is preserved, not dropped.** What disjointness uniquely provided
  — inferring the mode from the tool — is replaced by explicit recording in the
  audit log, which is strictly more reliable (it does not assume the tool is
  mode-unique).

## Consequences
- Protocols where two modes share a non-exclusive tool (e.g. `edit`) now load.
- A protocol where two modes share `approve` or `merge` still fails at load.
- Existing protocols remain valid: no shipped template shares an exclusive tool,
  and the defaults preserve the prior guarantee without any YAML edits.
- Audit-log consumers can no longer infer mode from tool name and must read the
  `mode` field instead.

## Alternatives considered
- Keep total disjointness: rejected — it blocks legitimate staged protocols and
  asserts more than the security property requires.
- Make `exclusive_tools` fully replaceable (shrinkable): rejected — allows a
  protocol to drop `approve`/`merge` and re-enable self-approval silently.
- Infer mode from a per-tool mode map at serve time instead of recording it:
  rejected — that again assumes tool uniqueness, which this ADR removes.
