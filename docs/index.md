# Snodo

**AI-native Software Development Lifecycle protocol engine.** AI agents as first-class team members, governed by declared policy with structural enforcement — bounded non-determinism, no trust required.

This is the documentation home. For the two-minute introduction, see the
[repository README](https://github.com/snodo-dev/snodo#readme).

## Core idea: policy vs mechanism

Declare what a valid development process looks like (`protocol.yml` — modes, validators, constraints, disagreement policies). The engine enforces it structurally: tokens issue only when validators agree, mutations require valid tokens, and a single `blocker` halts execution unconditionally regardless of policy. You write the policy; the engine provides the mechanism.

## 2+N model

Two human-in-control roles — **producer** (code generation) and **reviewer** (integration) — with structurally separated approval authority. Plus **N** specialised AI agents that operate within those roles. The model is validated at load time: an approval-conferring tool (`approve`, `merge`) held by two modes causes a WF1 violation and the protocol won't load.

[Architecture →](architecture.md)

## Get started

```bash
pip install snodo
snodo init --template team
snodo run "your first task" --mock
```

[Runbook →](runbook.md) — install, configure, the full CLI reference, MCP
serving, and troubleshooting.

## Coder backends

The coder writes; snodo governs, gates and records. `litellm` (default),
`opencode-cli`, `agy` and `mock` are supported; the container `opencode` path is
experimental. Which coder you pick does not change what is enforced.

[Coder backends →](coders.md)

## Plans

Plans are multi-wave work graphs you author, not generate. Dependencies run
between waves, not tasks, and a plan is re-verified on every load.

[Authoring a plan →](authoring-a-plan.md) · [Hand-authored plan runbook →](runbooks/hand-authored-plan.md)

## Machine interface

`--json` on `status`, `mode show`, `session show`, `task show`, and `worktree
list`, plus `snodo validate` for running a phase's validators without a coder.
Versioned with a `schema` field and validation-outcome exit codes (ADR 022).

[Machine interface →](machine-interface.md)

## The language

Every enforcement rule is declared in `protocol.yml` — modes, validators, severity caps, constraints, and disagreement policies. The full DSL reference is one document.

[Protocol reference →](protocol.md)

## Design rationale

Why PyJWT over custom HMAC? Why does warn withhold approval? Why ESCALATE → resolve → resume rather than auto-retry? Every design decision is documented as an ADR, extracted from the development audit log.

[Decisions →](decisions/README.md)

## Project status

Actively-developed research implementation (beta). This table is the measured
state of the code, not a promise.

| | |
|---|---|
| Code | ~32,700 lines of code (five packages — `snodo-core`, `snodo-tools`, `snodo-foundation`, `snodo-engine`, `snodo-mcp` — plus the root CLI/TUI) |
| Complexity | average cyclomatic complexity **A (4.96)** over 1,728 blocks (`radon`) |
| Lint / architecture | `ruff` clean; package layering enforced in CI by `import-linter` |
| Tests | ~3,900 collected; property-based tests over randomised inputs for the enforcement invariants |
| Python | 3.12 and 3.13 (CI matrix) |

The enforcement invariants — token integrity, capability boundaries,
non-overridable blockers, audit completeness — are verified by property-based
tests over randomized inputs.

## Research

The paper is under review; its architectural claims are mapped to code and tests
in [Research](research/README.md). Empirical studies live in `studies/`.
