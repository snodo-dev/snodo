# ADR 015 — Real validation on the MCP path and the four-outcome `validate_task` contract

## Status
Accepted

## Context
The MCP path (`snodo serve`) minted validation tokens from fabricated evidence:
`CoreToolHandler.handle_validate_task` appended a synthetic `severity="pass"`
result for every protocol validator and downgraded a failing test suite
(`blocker` → `warn`, "Tests (continuing)"). The engine path already ran the
real validators via `snodo.engine.validators.ValidatorRunner` and the policy
evaluator. The MCP path therefore had a fail-open gap: a token (INV1) that
authorises `dispatch_task` was issued without any validator actually running.

## Decision
`handle_validate_task` now runs the real validators — the same shared runner
the engine uses — and returns ONE of four discriminated outcomes:

| status            | token issued | who acts        | next action                                        |
|-------------------|--------------|-----------------|----------------------------------------------------|
| `pass`            | yes          | agent           | call `dispatch_task`                               |
| `escalate`        | no           | human           | `snodo authorize <decision_id>`, then re-validate  |
| `blocker`         | never        | agent → human   | fix code and re-validate; if exhausted, revise spec |
| `validator_error` | no           | operator        | retry / inspect logs                               |

Rules:
- **INV3** — a `blocker` is never overridable by a human decision. There is no
  "authorise past a blocker" path.
- **Ordering** — on `escalate` the token is minted only AFTER the human decision
  is recorded (out-of-band `snodo authorize`, then a re-call). A token is never
  returned alongside an escalation.
- **`validator_error` is distinct** — nothing is wrong with the work and there is
  no decision to make; it must not advise `snodo authorize`.

The shared logic lives in `snodo/validators/runner.py` (`run_validators`,
`dispatch_validator`, `resolve_validators`, `classify_outcome`) and is called by
BOTH the engine (`ValidatorRunner`) and the MCP handler. The pytest run remains
one validator result among the set; a failing suite is a `blocker`, not a `warn`.

## Consequences
- `snodo-mcp` now depends on `snodo-engine` (the validator implementations live
  there). Layering is unaffected (no cycle; engine does not import mcp).
- `validate_task` gained an optional `task_spec` argument so validators have a
  task to evaluate.
- Escalations are persisted to `session.checkpoint.decisions["pending_decisions"]`
  (engine shape) and resolved by the existing `snodo authorize` CLI + signed
  `DecisionRecord`s — no in-band (agent self-authorising) tool was added.
- The MCP instructions (the operating manual handed to the host agent) now
  describe the four-outcome contract.

## Deployment caveat (INV2)
On the MCP path snodo is ONE tool provider among several. The host agent may
have its own file-write or shell tools outside snodo's control, so INV2
(capability boundary) holds only if the host restricts the agent to snodo's
tools. This is a deployment assumption, not something snodo can enforce itself.

## Alternatives considered
- Keep the stub results but mark them as `warn` (cosmetic): rejected — still a
  fail-open gap and dishonest reporting.
- In-band approval (one-time code relayed through chat) / MCP elicitation:
  deferred — out-of-band `snodo authorize` is the chosen route for now.
- A mechanism to override a blocker: rejected outright (violates INV3).
