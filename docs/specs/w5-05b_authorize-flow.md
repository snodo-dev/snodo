# W5-05b: Authorize flow — propose, render, human-confirm, mint

## Intent
Close the HI-CTRL loop with minimal machinery. Agent proposes a decision
via MCP (unsigned, stored on the task). Human runs snodo authorize
<task_id> — the CLI loads the decision from task state, shows it, the
human eyeballs it against what the agent claimed and confirms. On yes,
the CLI (which holds the private key) mints an RS256 record carrying the
decision text. The consuming agent sees the signed accepted decision and
matches it to what it expected.

Security model (deliberately not a bank — positive intent assumed):
- Forgery prevented structurally: agent can't mint (no private key, W5-05a)
- Misrepresentation prevented by accountability: the human reads the
  CLI-rendered decision from stored state and confirms — the match is
  the human's responsibility
- Mismatch backstop: consuming agent can reject if accepted decision
  != expected (will be identical in practice)
- NO hash, NO proposal lifecycle state machine — the signature + human
  render + agent match is sufficient

## What to build

### Proposal storage
pending_decisions live at:
  session.checkpoint.decisions["pending_decisions"][task_id] = {
    "type": "adjudicate" | "set_model",
    "validator_id": "...",       # adjudicate
    "decision": "proceed",       # adjudicate
    "proposed_model": "...",     # set_model
    "scope": "...",              # set_model (validator:X | coder)
    "justification": "...",
    "proposed_by": "agent",
    "timestamp": "...",
  }

### MCP propose tools (mcp/decision_handlers.py, new)
DecisionToolHandler, following ModelToolHandler/JobToolHandler pattern.
- handle_propose_adjudicate(args): resolve active session (current_mode
  from state.json → get_active_session), write the proposal to
  pending_decisions[task_id] via session_mgr.update_decision. Return:
    {"status": "pending", "task_id": ...,
     "instruction": "Run: snodo authorize <task_id>",
     "proposal": {...}}
- handle_propose_set_model(args): same, type=set_model.
- Register in TOOL_REGISTRY (requires_token False) and call_tool dispatch.
- Mode gating: producer/relevant modes (follow existing tool gating).
- The MCP server resolves the active session the same way resolution.py
  already does (it has session write access via SessionManager).

### snodo authorize <task_id> (cli/commands/authorize_cmd.py, new)
- project_root = cwd (no walk-up yet)
- read_state → current_mode → get_active_session(mode, project_root)
- proposal = session.checkpoint.decisions["pending_decisions"][task_id]
- If absent: clear error ("no pending decision for task <id>")
- RENDER the proposal to the human (from stored state, not agent input):
  show type, target, decision, justification
- Prompt: "Authorize this decision? [y/N]"
- On y: signing_issuer() (private key), mint RS256 record carrying the
  decision fields (validator_id/decision/justification, or
  scope/proposed_model) — base64/standard JWT claims, signed
  - For adjudicate: write to decisions["decision_records"] (existing path)
  - For set_model: write to a decisions key the consumer reads (W5-05c
    defines application; here just mint+store the signed record)
- After mint: remove pending_decisions[task_id] (consumed)
- Audit: authorization minted
- authorize takes ONLY task_id — never accepts decision content from
  args. Content comes from stored proposal only.

### Consumption — agent match (light)
- The signed record carries the decision text. The consuming side
  (policy.py for adjudicate, already verifies via verify_only_issuer)
  exposes the accepted decision so the agent can match expected vs
  accepted. If mismatch: the agent may reject (non-deterministic, rare).
  No new structural enforcement — the signature already gates forgery.

## Acceptance criteria
- propose_adjudicate / propose_set_model write to pending_decisions on
  the active session, return the "run snodo authorize" instruction
- snodo authorize <task_id> renders the stored proposal, confirms, mints
  an RS256 record carrying the decision text
- authorize takes only task_id — decision content never comes from args
- adjudicate path: minted record works with existing policy.py verification
- pending_decisions[task_id] cleared after authorization
- Re-running authorize on an already-consumed task → clear "no pending
  decision" message (not a double-mint)
- No hash, no proposal hash verification
- Audit captures proposal-created and authorization-minted

## Testing
- Unit: propose_adjudicate writes proposal, returns instruction
- Unit: propose_set_model writes proposal
- Unit: authorize renders + mints from stored proposal (mock confirm=y)
- Unit: authorize with no pending decision → clear error
- Unit: authorize ignores any decision content not in stored proposal
  (only task_id is honored)
- Unit: after authorize, pending_decisions[task_id] removed
- Unit: minted adjudicate record verifies via policy.py path
- Full suite passes clean

## Constraints
- Read mcp/resolution.py (existing session-write pattern), mcp/server.py
  (call_tool dispatch, handler instantiation), adjudicate_cmd.py (current
  RS256 mint path), decisions.py (signing_issuer, issue_record payload),
  session.py (get_active_session, update_decision), infrastructure/state.py
  before touching anything
- authorize mints ONLY from stored proposal content, never from CLI args
  beyond task_id — this is the human-accountability anchor
- set_model APPLICATION is W5-05c — here just mint+store the signed record
- No hash binding, no proposal state machine — signature + render + match
