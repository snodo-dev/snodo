# ADR 041 — A module is a scope, not a mode

## Status

Accepted. Implementation tracked separately — this ADR records the boundary
before the field that crosses it exists.

## Context

Every protocol snodo has run so far governs a repository with one build. The
`solo`, `team`, `2+n` and `greenfield` templates all put a single
`tooling.test_command` behind the quality validators, and ADR 040 made the
coder observe that same declared runner. One repository, one test command, one
place where architectural decisions are recorded.

A monorepo is not that. `services/api` is a Python package with its own tests
and its own ADR folder; `apps/web` is a TypeScript package with different
tests, a different linter, and architectural decisions that have nothing to do
with the API's. A single `test_command` at protocol level cannot describe
either of them honestly: it either runs the whole repository's suite for a
one-line change in one package, or it runs the wrong suite. The `adr`
validator (`.snodo/protocol.yml`) reads "the Architecture Decision Records in
docs/decisions/ or docs/adr/" and in a monorepo there are several, most of
which are irrelevant to any given task and all of which are in the prompt.

This matters now because of intake. Retrofitting an existing messy repository
means analysing it, writing decision records for what is already there, and
producing a remediation backlog — and the unit that operation runs on cannot
be "the repository" for anything larger than a single package. Intake needs a
declared unit to be scoped to. So does readiness: a team adopting snodo does
not need the whole monorepo ready, only the module it is about to work in.

The obvious move is to make that unit a mode — `producer-api`, `producer-web`.
It does not survive contact with the compiler.

## Decision

**1. A module is a first-class scope in the protocol, and it is not a mode.**

`Protocol` gains a `modules` list. A module declares an identifier, the paths
it owns, where its decision records live, and its tooling:

```yaml
modules:
  - module_id: "api"
    paths: ["services/api/**"]
    decisions_path: "services/api/docs/adr"
    tooling:
      test_command: "uv run pytest services/api -q"
```

Modules are optional. A protocol with no `modules` behaves exactly as it does
today, and every existing protocol keeps compiling unchanged.

Three rules make a module declaration well formed: identifiers are unique
within the protocol, every module owns at least one path, and every validator
a module names is one the protocol already declares. Overlapping paths between
modules are a warning rather than an error — a monorepo legitimately keeps a
root `docs/` alongside per-package scopes, and nothing about the engine breaks
when a file falls in two modules; what breaks is the operator's expectation of
which one is authoritative, and a warning naming both modules and the
overlapping prefix says that better than a refusal to compile.

These are schema checks, and they are deliberately **not** a numbered
well-formedness condition. WF1 through WF5 are the governance invariants of
the paper's Section 4.4 — mode separation, role uniqueness, validator
coverage, policy completeness, constraint consistency — and each is a
statement about authority. "This identifier is unique" is not of that class.
Adding a WF6 would make the sequence mean two different things at once and
would put a schema rule in front of a reader who has been told the sequence is
about who may approve what. Module declaration errors are their own named
exception, outside the WF numbering.

**2. Modes stay roles. Modules cannot be expressed as modes.**

This is not a preference. WF1 (`ProtocolVerifier.check_wf1`,
`packages/snodo-foundation/src/snodo/compiler/verifier.py:102`) requires that
an exclusive tool appear in at most one mode, and `exclusive_tools` defaults
to `{approve, merge}` with `Protocol.validate_exclusive_tools` unioning the
defaults back in on every load, so a protocol cannot shrink the set. Two
producer modes both holding `merge` is a `WF1Violation`; the only way to
compile mode-per-module is to take `merge` away from every module but one,
which means a module cannot merge its own work.

The invariant is right and the encoding was wrong. A mode answers *what kind
of work this is and who judges it*: tools, validators, disagreement policy,
authority. A module answers *where the work happens*: paths, tooling, decision
records. They are orthogonal, and enumerating their product is what forces the
collision — four roles across twelve packages is forty-eight modes, each one
holding or not holding `merge`. Composing them at run time costs nothing:
a task declares its module, the session declares its mode, and the pair
resolves.

**3. Tooling resolves module first, then validator, then protocol.**

`Validator.tooling` already carries a `test_command`
(`packages/snodo-foundation/src/snodo/compiler/models.py:141`) and the
protocol carries a default. A module's `tooling` overrides both for tasks
scoped to it. When a task declares no module, resolution is exactly what it is
today, so the change is additive at the point where it would otherwise be
riskiest.

**4. A module may narrow or extend the mode's validator set, and no more.**

`web` may add an accessibility validator that `api` does not run. A module
must not remove a validator that would otherwise judge the work in a way that
weakens the gate: the module list is intersected with the protocol's declared
validators and cannot introduce one the protocol has not defined. Adjudication,
the disagreement policy and the merge gate are untouched by module scope.

**5. The `adr` validator reads the module's `decisions_path` first, then the root.**

Repository-wide decisions stay at the root and apply everywhere. Module-local
decisions apply to their module. Precedence between records is unchanged —
highest-numbered wins on a topic, an explicit supersedes wins over that.

**6. Modules scope the operator surface, not the engine's guarantees.**

`snodo run --module api`, `snodo ready --module api`, `snodo intake
services/api`. One `.snodo/` at the repository root, as today: the protocol,
the audit log and the session are repository-level and remain so. A module is
a lens over one repository, never a second repository.

## Consequences

The audit log gains an optional `module_id` on task-scoped events. Nothing is
removed from any payload and no event type changes shape, so a consumer that
does not know about modules reads the stream as it always did.

Readiness becomes decomposable: a per-module score with a repository headline
that is a roll-up rather than an average, so a monorepo with one healthy
package and eleven neglected ones cannot present as "58% ready". This is the
form the score has to take for the adoption path to work at all — make one
module ready, work there, expand outward.

Intake gets its unit. Discovery of module boundaries is a separate, cheap and
deterministic problem — workspace members in `package.json`, `pyproject.toml`,
`Cargo.toml`, `go.mod` files, project files, existing ADR folders — and its
output is a proposed `modules` list the operator edits before anything
expensive runs. That belongs in the intake ADR, not this one.

What this deliberately does not do: it does not let a module carry its own
protocol, its own audit chain, its own session, or its own merge authority. A
module that needs those is a separate repository, and snodo already governs
repositories.

The paper's claims are unaffected. WF1 continues to hold in its current form —
one mode holds `merge`, repository-wide — and it holds *because* modules are
not modes. The adjudication path, the halt taxonomy and the hash-chained audit
log are unchanged. The only claim that needs a sentence is that validator
tooling resolves per module where a module is declared.
