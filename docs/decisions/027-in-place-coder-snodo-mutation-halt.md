# ADR 027 — In-place coder .snodo/ mutations are detected and halt as a blocker

## Status

Accepted

## Context

ADR 026 protects `.snodo/` (the protocol and governance state an agent is
judged by) at the tool surface: `WorkspaceMCP` write/delete/mkdir and
`GitMCP.stage_files` refuse paths under `.snodo/`. For coders that write
through `WorkspaceMCP` (litellm, mock) that is enforcement — the mutation is
refused before it happens.

`OpenCodeCLIAdapter` and `OpenCodeAdapter` are different. They set
`skip_workspace_write = True` and write to the working tree **in place**
(opencode writes files directly on the host, or into a volume-mounted
workspace) and read changes back via git. They never go through
`WorkspaceMCP`, so the tool-surface boundary is bypassed entirely.

ADR 026's answer for these adapters was to **filter** `.snodo/` paths out of
the returned `CodeArtifact`. That is reporting, not enforcement: dropping the
entry from the artifact list does not undo the write, and in the normal
post-init state `.snodo/` is gitignored and untracked, so the git readback
cannot see a `.snodo/` write at all. The mutation stayed in the working tree,
absent from the artifact report and from the audit trail.

The issue (#52) asked for three things: detect rather than drop; consider
whether it should halt; and make the behaviour a property of the adapter base
class, not repeated per adapter.

## Decision

1. **In-place coders get a base class, `InPlaceCoderAdapter`** (in
   `snodo.coders.base`). It owns `skip_workspace_write = True` and
   `skip_engine_commit = True`, and wraps the coder call in a `.snodo/`
   snapshot: the subclass implements `_implement_in_place(spec)`, and the base
   class snapshots `.snodo/` before the call, then compares after it. If any
   path under `.snodo/` changed, it raises `SnodoMutationError` naming the
   paths.

2. **Detection is a filesystem snapshot, not git.** Because `.snodo/` is
   normally gitignored, git readback cannot see a mutation there; only a
   content comparison of the directory around the coder call is reliable. The
   snapshot window is the coder call itself, so the engine's own bookkeeping
   under `.snodo/` (audit log, sessions, state.json) — which happens outside
   this window — is never misattributed to the coder.

3. **A `.snodo/` mutation is a blocker halt, not a revert.** Reverting via
   `git checkout -- .snodo/` is a no-op for the gitignored/untracked files
   that are exactly the primary scenario, and a revert would launder a
   governance violation into "no change detected". The engine's
   `_execute_node` catches `SnodoMutationError`, sets a terminal `blocked`
   halt, marks post-validation skipped (never a green verdict on a mutated
   tree), and records a `snodo_mutation_blocked` audit event naming the paths.
   The tree is left for operator inspection.

4. **The artifact filter is removed from the in-place adapters.** Their
   `_diff_to_artifact` keeps `.snodo/` entries — evidence must not be silently
   discarded. Enforcement moved to detection + halt in the base class, so a
   future in-place adapter cannot reintroduce the hole by forgetting the
   filter.

5. **In-process adapters are unchanged.** litellm and mock can only write
   through `WorkspaceMCP`, which refuses `.snodo/` writes (ADR 026). Their
   `.snodo/` artifact filtering remains as defense-in-depth; it is not the
   enforcement point for them.

## Why halt (not just detect and continue)

An agent writing to `.snodo/` is either confused or attempting to widen its
own capability; both are worth a blocker. A warn or a silent revert would let
the task continue to post-validation and potentially complete — a green result
on work that attempted to mutate the rules it is judged by. Halt is the honest
outcome in the four-outcome vocabulary: a `.snodo/` mutation is a
non-overridable, structural violation, not a judgement about the work and not
an operational fault.

## Consequences

- `OpenCodeCLIAdapter` and `OpenCodeAdapter` now raise `SnodoMutationError`
  and the engine halts with a `blocker` when a coder mutates `.snodo/`.
- The mutation remains on disk for operator inspection; nothing is silently
  reverted or dropped.
- A `snodo_mutation_blocked` audit event records the attempt and the paths.
- Adapters that write in place are structurally required to inherit the base
  class and implement `_implement_in_place`; the `.snodo/` guard is inherited,
  not repeated.

## Alternatives considered

- **Filtering (status quo):** rejected — removes the only evidence of a
  mutation that already happened.
- **Revert via `git checkout -- .snodo/`:** rejected — a no-op for gitignored
  files, and launders a violation into "no change".
- **Warn and continue:** rejected — lets a task that attempted to mutate its
  own governance reach a possibly-green completion.
