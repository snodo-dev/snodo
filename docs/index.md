# Snodo

**AI-native Software Development Lifecycle protocol engine.** AI agents as first-class team members, governed by declared policy with structural enforcement — bounded non-determinism, no trust required.

## Core idea: policy vs mechanism

Declare what a valid development process looks like (`protocol.yml` — modes, validators, constraints, disagreement policies). The engine enforces it structurally: tokens issue only when validators agree, mutations require valid tokens, and a single `blocker` halts execution unconditionally regardless of policy. You write the policy; the engine provides the mechanism.

## 2+N model

Two human-in-control roles — **producer** (code generation) and **reviewer** (integration) — with structurally separated tool sets. Plus **N** specialised AI agents that operate within those roles. The model is validated at load time: overlapping tools cause a WF1 violation and the protocol won't load.

[Architecture →](architecture.md)

## Get started

```bash
pip install snodo
snodo init --template team
snodo run "your first task" --mock
```

[Runbook →](runbook.md)

## The language

Every enforcement rule is declared in `protocol.yml` — modes, validators, severity caps, constraints, and disagreement policies. The full DSL reference is one document.

[Protocol reference →](protocol.md)

## Design rationale

Why PyJWT over custom HMAC? Why does warn withhold approval? Why ESCALATE → resolve → resume rather than auto-retry? Every design decision is documented as an ADR, extracted from the development audit log.

[Decisions →](decisions/)
