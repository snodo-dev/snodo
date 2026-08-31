# ADR 035 — Declared coder-adapter capability interface; "coder produced nothing" is always a fault

## Status

Accepted

## Context

The coder seam between snodo and its coder adapters was implicit
(`docs/architecture/coder-adapter-contract.md`). The engine offered each
adapter optional capabilities — a progress sink, a workspace, correlation ids,
and two behavioural switches (`skip_workspace_write`, `skip_engine_commit`) —
but reached for them with `hasattr` guards:

```python
if hasattr(coder, "progress_callback"):
    coder.progress_callback = self._progress
if hasattr(coder, "_job_id"):
    coder._job_id = job_id
```

The consequence is structural (contract §3.1): **any capability the engine
offers optionally is one some adapter will silently lack**, and the absence is
indistinguishable from the capability not existing at all. When per-turn
progress reporting landed behind that guard, the opencode adapters — which do
not define `progress_callback` — were silently skipped, and the feature simply
did not exist on two of four adapters for weeks with no error, warning, or
failing test.

The same document (§4) names a second fault of the same class:
`skip_engine_commit = True` downgraded "coder produced nothing" from a hard
`ExecutionError` (the litellm path) to an audit note (the opencode path,
`executor.py:52-59`). The same fault was loud on one path and quiet on another,
because the flag was treated as a permission to opt out of a mechanism with no
transfer of the responsibility that mechanism carried.

## Decision

1. **The optional capabilities are DECLARED on the `Coder` ABC, with
   base-class defaults.** `workspace_mcp`, `progress_callback`, `_job_id`,
   `_task_id`, `model`, `skip_workspace_write`, and `skip_engine_commit` all
   have defaults on the ABC. The engine assigns them **unconditionally** —
   never behind a `hasattr` guard. "This adapter does not support X" is now a
   visible fact: an adapter that does not override a capability inherits a
   default the engine and tests can see and assert on, instead of being a
   silently skipped line.

2. **A conformance test asserts every registered adapter carries the declared
    interface**, so a future adapter that drops a capability fails at the branch
    rather than surfacing months later (the same pattern ADR 027 established for
    the `.snodo/` guard, and that the existing channel-A/B conformance test
    established for observability).
    *Amended 2026-08-30 (ADR 039, #143): since the engine builds the
    validator/classifier client from configuration when the coder supplies none,
    the conformance set covers more than interface carriage. Its per-adapter
    graph runs (parametrized over every registered coder, e.g.
    `test_commit_not_happening_is_refused`) drive the protocol's gates — real
    `llm_check` pre-execute and `acceptance` post-execute validators — through
    the engine's own completion-function resolution. The suite therefore also
    asserts the gates actually run under every adapter: no adapter passes on a
    fabricated LLM capability, and no adapter halts on a missing one.*

3. **"Coder produced nothing" raises `ExecutionError` on every path.**
   `skip_engine_commit` controls *who commits*, not *whether observable work
   was produced*. Opting out of the engine's commit mechanism does not waive
   the obligation that the coder produce observable, attributable work. The
   `empty_artifact_warning` audit note is removed.

## Principle

> Opting out of a mechanism must not silently discharge the responsibility
> that mechanism carried.

The commit is a mechanism; producing observable work is a responsibility. A
flag that stops the engine from committing must not also stop the engine from
noticing that nothing was produced.

## Consequences

- Every registered adapter exposes the declared capabilities; the opencode
  adapters now carry `progress_callback` and the correlation ids (inherited
  defaults) instead of silently lacking them.
- The engine's `_prepare_coder` and graph construction set the capabilities
  unconditionally; a future optional capability must be added to the ABC and
  the conformance test, never behind a new `hasattr` guard.
- A no-op opencode run now fails loudly as `internal_error` (ExecutionError)
  like the litellm path, instead of continuing quietly with an audit note.
- `empty_artifact_warning` audit events no longer exist; existing callers of
  that event name will find it absent by design.

## Alternatives considered

- **A separate mixin/trait per capability:** rejected — over-engineering for
  seven attributes; a single declared interface with defaults is simpler and
  every attribute is either an injected value or a switch the executor reads.
- **Feature-detection protocol (e.g. `__getattr__` reporting unsupported
  attributes):** rejected — the conformance test already makes the absence
  fail loudly at the branch, which is cheaper and simpler than a runtime
  protocol.
- **Keep the audit note but make it a warn/escalate:** rejected — a no-op run
  is a fault, not a judgement about the work; the four-outcome vocabulary
  already has `internal_error` for execution faults.
