# ADR 047 — An MCP tool is governed by the protocol, not by a token the caller must hold

## Status

Accepted. Supersedes [ADR 017](017-wf1-exclusive-tools.md) as the record of what
governs the MCP tool surface; ADR 017's load-time rule — WF1 checks exclusivity
of approval-conferring tools, with mode attribution in the audit log — remains
in force as part of the protocol's governance, and is not weakened here.

## Context

The MCP server enforced a rule it called WF1 at the tool surface: every tool
schema carried a `requires_token` flag and `_enforce_wf1` refused the call when
the server held no validation token. That gate is not doing the job it was
built for. Which tools a caller may use is already decided by the protocol —
the mode's capability grant (`MODE_TOOL_MAP`) filters what a server exposes,
and `check_wf1` refuses to load a protocol that lets two modes hold an approval
tool. What a validator quorum concluded is already enforced inside the engine
loop: every dispatched task is validated again before execute, a `blocker`
routes the work back, an `escalate` halts until a human signs a decision. What
survived at the MCP layer was only a demand that the *caller* hold a token —
and the caller is an orchestrator or a human, who has no business carrying one.

The flags were also incoherent where they bit hardest. On the plan surface,
`propose_plan`, `decompose` and `generate_spec` required a token while
`run_plan` — the call that starts a run which spawns coders and mutates the
repository — required none: authoring gated, execution not. And the only token
the surface gate accepted came from `validate_task`, so authoring a plan
demanded a verdict about a task the plan had not yet created — a cycle at the
top of the workflow. Observed on a real project: an orchestrator was refused
`propose_plan` for want of a token, on a call whose own description says
nothing executes.

## Decision

**Remove the token requirement from the MCP tool surface.** The
`requires_token` flag is gone from every tool schema and `_enforce_wf1` is
deleted; a tool call is never refused for want of a token the caller holds.
Which tools exist and which a mode grants is unchanged.

**What governs tool access now is the protocol and the mode:**

- At compile time: WF1 exclusivity of approval-conferring tools
  (`check_wf1`), unchanged from ADR 017 — a protocol cannot grant `approve` or
  `merge` to two modes.
- At serve time: INV2 capability filtering — a server exposes exactly the
  concrete tools the active mode(s) grant, and rejects anything else as
  unknown.
- In the loop: the engine's own token discipline, untouched — a quorum still
  gates what the loop does (issuance on `pass`, verification and single-use
  consumption at the engine's execute boundary), a blocker still sends work
  back, and an escalate still requires a human decision through `authorize`.

**At the MCP surface the token stays what the engine's vocabulary makes it
available as: evidence, not a gate.** `validate_task` still runs the real
pre-execute quorum and returns the four outcomes of ADR 015; a `pass` (or an
`escalate` adjudicated by a human) records a single-use token that the next
`dispatch_task` consumes as the audit link between a satisfied quorum and the
work dispatched. The server's instructions are rewritten to say this, because
every orchestrator that connects reads them.

## Consequences

- An orchestrator can call any tool its mode grants with no token held; the
  `WF1 violation` refusal and the `wf1_violation` audit event no longer occur
  at the surface.
- The plan-authoring cycle is gone: `propose_plan`, `decompose` and
  `generate_spec` behave the way their descriptions already said they do —
  nothing executes, nothing is demanded of the caller.
- No guarantee moves or weakens: irreversible work is still quorum-gated where
  it happens — per task, inside the engine loop — and the human gate on an
  escalation is still a signed decision, which an agent cannot mint.
- Audit consumers keep the `dispatch_request` / `token_consumed` events as the
  pre-check-to-dispatch link; a dispatch with no token on record is simply a
  dispatch whose enforceable judgement is the loop's own.

## Alternatives

- **Keep the surface gate and add a token for plan authoring**: rejected — it
  doubles down on a demand the caller cannot meaningfully satisfy, and an
  orchestrator holding a token for `propose_plan` would assert a verdict about
  a task that does not exist yet.
- **Gate only `dispatch_task` at the surface**: rejected — the engine's run
  re-validates and consumes at its own execute boundary already; a second
  copy of the gate at the surface adds a state machine that can drift from the
  thing it guards and protects nothing the loop does not.
- **Remove tokens from the MCP server entirely (validate_task stops
  recording)**: rejected — the four-outcome contract (ADR 015) and the
  single-use discipline (ADR 016) are the vocabulary the engine shares, and
  the recorded-then-consumed token is the audit link between a pre-check and
  the work; removing the gate needs no removal of the evidence.
