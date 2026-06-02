# Snodo Architecture

How enforcement works from top to bottom. For individual design decisions, see [ADRs](decisions/).

## Overview

Snodo is a **policy-vs-mechanism** engine: you declare what a valid software development process looks like (`protocol.yml`), and the engine enforces it structurally — no after-the-fact review, no trust in agent compliance. AI agents participate as first-class team members, gated by the same rules as human contributors.

The 2+N model underlies everything: **2** human-in-control roles (producer and reviewer) with disjoint tool sets, plus **N** specialized AI agents that operate within those roles. Mode separation is structural — the engine refuses to load a protocol where two modes share a tool (WF1), and every mutating operation requires a cryptographically valid token that can only be issued by a satisfied validator quorum (INV1/INV3).

## Key concepts

| Concept | Mechanism | Invariant |
|---------|-----------|-----------|
| Mode separation | Disjoint tool sets, verified at load time | WF1 |
| Validator quorum | N validators vote; policy decides proceed/block | Decision flow below |
| Non-overridable block | Any `blocker` halts before policy logic | INV3 |
| Token-gated mutations | Mutating MCP tools require JWT validation token | WF1, INV1 |
| Audit immutability | Hash-chained event log, append-only | INV4 |
| Session resumability | File-backed checkpoint per (mode, project) | INV5 |
| Recovery loop | Failed tasks re-enter governance on resume | Kleene closure |
| Coder independence | Adapter pattern over LLM backends | Coder adapter |

## Decision flow — how a task is evaluated

```
Governance → Validate → [Execute] → Post-validate → [Move-next] → Complete
     ↑                         |                          |
   Resolution              Blocked (ESCALATE)        Blocked (HALT/ESCALATE)
```

1. **Governance**: Checks iteration bounds (50 max), consumes any pending resolution. If the session has a `proceed` decision for this task, `resolution_override` is set and validation is skipped. If `halt`, the task is blocked immediately.

2. **Validate** (`pre_execute`): Runs validators configured for the current mode and phase. Results feed into the `PolicyEvaluator`:
   - `blocker_count > 0` → **HALT** (INV3 — unconditional, all policies)
   - Threshold on `pass_count` per policy: unanimous needs all, majority needs >half, quorum needs ≥0.67×total, any needs ≥1
   - `warn` withholds approval — does NOT count toward the pass threshold
   - Threshold met → token issued → proceed to execute
   - Threshold not met → **ESCALATE** → `pending_disagreement` populated → task blocked, human resolves

3. **Execute**: The coder generates code artifacts. Files are written via WorkspaceMCP, staged and committed via GitMCP. Every mutation requires a valid JWT token (WF1 enforcement at the MCP server layer).

4. **Post-validate** (`post_execute`): Runs post-execute validators (e.g., quality/test-runner). Same policy evaluation. Can ESCALATE or HALT after execution.

5. **Move-next**: Marks task complete. Transitions are declarative — documented in the protocol, not engine-executed.

## Mode model + infrastructure boundary

Each mode declares a set of **logical tools** (edit, approve, pr, etc.) that map to **concrete MCP operations**. Two modes never share a tool — WF1 verifies this at load time (`verifier.py:check_wf1()`).

Two MCP servers can be served from one protocol:
```bash
snodo serve --mode producer  # edit, dispatch, test, validate
snodo serve --mode reviewer  # review, approve, merge, pr
```

The orchestrator connects to both servers, routing operations through the appropriate mode. Each server's tool set is the logical tools' concrete MCP operations, with read-only operations requiring no token and mutations requiring a valid JWT.

## Validator quorum → token issuance → gated mutations

This is the core enforcement chain:

1. Validators evaluate the task spec and emit `pass` / `warn` / `blocker`
2. `PolicyEvaluator` combines results per the disagreement policy (`policy.py:88-137`)
3. If the policy permits and no blockers exist, `TokenIssuer` issues a JWT (`tokens.py:86-141`)
4. The MCP server's `_enforce_wf1()` checks the token before every mutation (`server.py:536-559`)
5. Without a token — no writes, no commits, no merges

The chain is structural: you cannot bypass validation by skipping a step. You need:
- A satisfied validator quorum → a token → the ability to mutate
- None of these can be forged (JWT signed, verifiable) or skipped (WF1 enforced at the boundary)

## Audit log (INV4)

Every event — governance checks, validations, dispatches, completions, halts — is recorded in a hash-chained append-only log (`audit.py:19-231`). Each event has:
- `sequence`: monotonically increasing
- `previous_hash`: SHA-256 of the prior event
- `event_hash`: SHA-256 of this event's full payload

The chain is verifiable: `verify_chain()` recomputes every hash against the stored chain and returns false if tampered. The log is thread-safe (single lock wraps append + disk write).

## Session checkpoint (INV5)

Session state is persisted per (mode, project) as JSON files under `~/.snodo/sessions/`. Each session carries:
- `session_id`: timestamped unique identifier
- `mode`, `project_root`, `project_id`: scoping triple
- `checkpoint`: current task reference, pending decisions, memory summary, last-updated timestamp

On restart, `get_active_session()` finds the matching session by mode + project hash. Resolution decisions (`proceed` or `halt` for escalated tasks) are stored in `checkpoint.decisions` and consumed on the next governance pass.

## Adapter pattern

Coders implement a single interface:
```python
class Coder(ABC):
    def implement(self, spec: TaskSpec) -> CodeArtifact:
        ...
```

Two shipped adapters:
- **LiteLLMAdapter** (`coders/litellm.py`): routes to ~100+ LLM providers via litellm
- **MockAdapter** (`coders/mock.py`): deterministic stub for testing

Code-host providers follow the same pattern (`providers/registry.py:detect_provider()` → GitHub or local).

## Kleene closure

Subtasks spawn recursively: a completed task can dispatch sub-work. Each subtask runs the full governance loop independently. The engine bounds recursion depth (`max_subtask_depth`, default 3) and iteration count (50 max per task, configurable) to prevent runaway loops.

## Invariant → mechanism table

| Invariant | Mechanism | Source |
|-----------|-----------|--------|
| WF1 — Mode separation | Disjoint tool sets, load-time verification | `verifier.py:check_wf1()` |
| WF2 — Role uniqueness | Duplicate detection, load-time verification | `verifier.py:check_wf2()` |
| WF3 — Validator coverage | Missing validator detection; initial mode existence; dispatch requires pre_execute | `verifier.py:check_wf3()` |
| WF4 — Policy completeness | Policy-to-validator-count matching | `verifier.py:check_wf4()` |
| WF5 — Constraint consistency | Unique IDs; registered predicate verification | `verifier.py:check_wf5()` |
| INV1 — Token integrity | JWT HS256, expiry, task binding | `tokens.py:86-247` |
| INV2 — Mode boundary | MCP server filters tools by mode | `server.py:443-458` |
| INV3 — Non-overridable block | `blocker_count > 0 → HALT` before policy logic | `policy.py:117-123` |
| INV4 — Audit immutability | Hash-chained append-only log | `audit.py:30-231` |
| INV5 — Session resumability | File-backed checkpoint per (mode, project) | `session.py:39-348` |
