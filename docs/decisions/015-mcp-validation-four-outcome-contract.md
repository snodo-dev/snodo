# ADR 015 — Real validation on the MCP path and the outcome contract

## Status
Accepted. Amended 2026-09-13: the canonical outcome vocabulary is **five**, not
four — `environment_error` is the fifth value and is not a verdict about the
task. The filename keeps its original "four-outcome" name.

## Context
The MCP path (`snodo serve`) minted validation tokens from fabricated evidence:
`CoreToolHandler.handle_validate_task` appended a synthetic `severity="pass"`
result for every protocol validator and downgraded a failing test suite
(`blocker` → `warn`, "Tests (continuing)"). The engine path already ran the
real validators via `snodo.engine.validators.ValidatorRunner` and the policy
evaluator. The MCP path therefore had a fail-open gap: a token (INV1) that
authorises `dispatch_task` was issued without any validator actually running.

The four-outcome contract this ADR first recorded left one halt mis-taxonomised.
A coder binary absent from PATH (or an unreachable container runtime) halted as
`execution_error`, whose canonical value was `blocker` — recording a verdict
about the task's content for a fault that had nothing to do with the task, and
routing the next attempt into recovery against a spec that had passed every
validator (issue #195).

## Decision

`handle_validate_task` runs the real validators — the same shared runner the
engine uses — and returns ONE of four discriminated validation outcomes:

| status            | token issued | who acts        | next action                                        |
|-------------------|--------------|-----------------|----------------------------------------------------|
| `pass`            | yes          | agent           | call `dispatch_task`                               |
| `escalate`        | no           | human           | `snodo authorize <decision_id>`, then re-validate  |
| `blocker`         | never        | agent → human   | fix code and re-validate; if exhausted, revise spec |
| `validator_error` | no           | operator        | retry / inspect logs                               |

The engine's **canonical outcome vocabulary** — the value persisted as both
`halt_type` and `final_decision` in the halt payload, with the precise cause
preserved in `raw_halt_type` — is **five**. `environment_error` is the fifth:

| outcome             | kind of halt                    | who acts      | next action                                                        |
|---------------------|---------------------------------|---------------|--------------------------------------------------------------------|
| `escalate`          | a verdict needing adjudication  | human         | `snodo authorize <decision_id>`, then re-run                       |
| `blocker`           | a verdict about the work        | agent → human | fix the work (or the spec), then re-run                            |
| `validator_error`   | a validator produced no verdict | operator      | retry / inspect logs                                               |
| `internal_error`    | an engine defect                | operator      | retry / inspect logs                                               |
| `environment_error` | the coder could not be invoked  | operator      | install the missing program where the run executes, re-run unchanged |

`pass` is the token-issuing success, not a halt, so it is not in that table.
`validate_task` never invokes a coder and so never returns
`environment_error`; that value is reached only when an execution halt is
classified.

Rules:
- **INV3** — a `blocker` is never overridable by a human decision. There is no
  "authorise past a blocker" path.
- **Ordering** — on `escalate` the token is minted only AFTER the human decision
  is recorded (out-of-band `snodo authorize`, then a re-call). A token is never
  returned alongside an escalation.
- **`validator_error` is distinct** — nothing is wrong with the work and there is
  no decision to make; it must not advise `snodo authorize`.

### Why `environment_error` is none of the four

An environment fault — the coder binary is absent, the container runtime is
missing, the backend cannot be started — is none of the four canonical halts:

- `blocker` is a verdict about the task's content; nothing here is about the
  task. Folding it into `blocker` is the laundering #195 was written against,
  and `execution_error -> blocker` reintroduced it one layer down.
- `escalate` asks a human to adjudicate a verdict about the work
  (`snodo authorize`); no adjudication installs a program.
- `validator_error` claims a validator failed to produce a verdict; the
  validators unanimously produced one — of the task, about the task.
- `internal_error` claims an engine defect; #195 explicitly refuses to launder
  an operator-fixable coder fault into that.

Naming it is the honest taxonomy: a fifth canonical value rather than a lie
inside an existing one.

### What a consumer written against four does with it

Handle `environment_error` the way you handle `internal_error` — a non-verdict
operational halt: stop the run, surface the payload's `hint` (it carries the
install command), do NOT retry the task against its recorded failure, and do NOT
treat it as authorisation state. Any canonical value outside the four must fall
into that same non-verdict branch, never the blocker branch: defaulting an
unknown outcome to "a verdict about the work" is the error class this mapping is
made of.

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
- The MCP instructions describe the validation outcomes and name
  `environment_error` as the non-verdict operational halt, pointing here.
- This ADR is the home of the taxonomy and its reasoning. Source comments
  (`writeback.py`, `validation.py`, `executor.py`) and the plan layer state only
  where a value is applied and point here; they do not restate why it exists.

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
- Fold the missing-program fault into `blocker` (or `internal_error`): rejected —
  it records a verdict about work that was never judged, or an engine defect
  that does not exist (see "Why `environment_error` is none of the four").
