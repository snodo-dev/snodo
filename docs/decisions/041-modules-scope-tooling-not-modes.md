# ADR 041 — A module is a scope, not a mode

## Status

Accepted. Implementation tracked in #233.

## Context

The compiled protocol can describe at most one build. Every template places a
single `tooling.test_command` behind the quality validators and a single
`decisions_path` behind the ADR validator. A monorepo therefore faces a forced
choice: declare one test command that covers the whole tree (running every
package's suite when a one-line change touches only one), or leave the field
empty and have the quality validator fire blind.

snodo's own repository is the concrete case: six packages live under
`packages/`, each with its own `pyproject.toml` and its own test runner
invocation. The next project to onboard has the same shape with unrelated
stacks — one package in Python, one in Rust, one in TypeScript.

The obvious fix — add a `module` field to `Mode` — is wrong. A mode is an
operational stage: planning, implementation, review. A Python package and a
Rust package do not live in different stages; they coexist in the same stage
with different tooling. Binding the scope to the stage would require a separate
mode for each package, multiplying the mode graph by the number of packages,
and collapsing WF1 (exclusive tools must appear in at most one mode) for any
tool the modes share.

## Decision

**The unit for scope is a module, and a module is a scope rather than a mode.**

A module is a structural declaration that partitions the repository into named
regions. It is not an operational stage. Two modules can be active in the same
mode. A module does not change how the engine dispatches work; it records what
belongs where so that tooling and decision records can be resolved correctly
for a given path.

A module declares:

- **identifier** — unique within the protocol; slug-safe alphanumeric with `-`
  and `_`.
- **paths** — one or more root paths this module owns. At least one path is
  required; a module without a path is not a scope.
- **decisions_path** — optional directory where this module's decision records
  live. When absent, ADR tooling falls back to the protocol-level setting.
- **tooling** — optional per-module tooling map (same shape as the existing
  `tooling` field on validators). When absent, tooling falls back to the
  protocol-level setting.
- **validators** — optional list of validator IDs that apply within this
  module. Every ID must name a validator already declared in the protocol;
  a module cannot introduce a validator the protocol does not define.

`Protocol` gains an optional `modules` field (list of `Module`). A protocol
that does not declare a `modules` list must compile and behave exactly as it
does today; the field is additive and backward-compatible.

## Well-formedness (WF6)

Well-formedness rule WF6 is added to the verifier alongside WF1–WF5:

1. **Identifier uniqueness.** No two modules may share an identifier.
2. **Non-empty paths.** Every module declares at least one path.
3. **Validator reference closure.** Every validator ID in a module's
   `validators` list must be declared in the protocol's top-level `validators`
   list. The verifier names the unknown identifier in the error.

### Overlapping paths: warning, not error

Two modules that claim overlapping paths are a warning, not an error. The
reasoning: a monorepo legitimately places a `docs/` directory at the root that
is shared across all packages. Treating overlap as an error would prevent the
natural pattern of a top-level `docs` module (owning `docs/`) that coexists
with per-package modules that each own `packages/foo/`. Overlap is almost
always an authoring mistake rather than a safety violation — there is no
correctness invariant that breaks when paths overlap, only ambiguity about
which module is authoritative for a file that falls in both — so the verifier
emits a warning that names both modules and the overlapping prefix rather than
refusing to compile.

## Non-scope

- Runtime consumption of `modules` is not part of this change. No validator
  reads the new field. No CLI flag is added.
- `Mode`, WF1–WF5, and `exclusive_tools` are untouched.
- The `modules` field does not change how the engine dispatches tasks.

## Consequences

- A protocol with no `modules` field is unchanged in every observable respect.
- A two-module protocol round-trips through compile and serialise without loss.
- Each WF6 violation produces a message naming the offending module identifier.
- Consumers that do not yet know about modules ignore the field; they see an
  ordinary `Protocol` with a `modules` attribute that is an empty list or a
  list of typed objects, and need not change.
