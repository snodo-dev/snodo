# Spec: HI-CTRL DecisionRecord — unforgeable adjudication of escalations

## Why

The protocol has the require_human POLICY and the HI-CTRL ROLE but no MECHANISM for the
human decision. The current resolve (resolution.py, loop.py:225-260) is severity-blind,
single-use, keyed only by task_id, and VIOLATES INV3: a human "proceed" sets
resolution_override=True (loop.py:234-244) which bypasses the token gate (loop.py:404)
even for a genuine blocker. This both adds the missing mechanism and fixes that breach.

A DecisionRecord is the human-side analog of the validation token: a signed, unforgeable
credential (reusing the tokens.py JWT/HS256 infra, INV1), audited (INV4), persistent
(fixes the resolve-then-re-dispatch race), consulted at the policy layer AFTER the blocker
check so blockers are never overridable (INV3 preserved by construction).

## Part 1 — Remove the INV3 violation (do this regardless)

Delete the severity-blind override path: loop.py:234-244 (setting resolution_override) and
the bypass at loop.py:404. A human decision must NEVER bypass a blocker's token gate. This
is a correctness fix independent of the rest.

## Part 2 — DecisionRecord primitive (new infrastructure/decisions.py)

- A signed JWT (HS256, SAME secret/infra as infrastructure/tokens.py), parallel to the
  validation Token. Claims: task_ref, validator_id, adjudicated_severity (must be a
  non-blocker: warn/escalation — reject "blocker"/"error"), adjudicated_justification (the
  validator's concern text it was made against, for audit/drift), decision (proceed|halt),
  justification (human's reason), resolved_by (human id), iat.
- PERSISTENT: no short single-use TTL. Unlike validation tokens (which expire to force
  revalidation), a DecisionRecord is a durable human adjudication that should apply on
  re-dispatch.
- DecisionRecordIssuer: issue_record() / verify_record() (signature + task binding), audit
  on both. Mirror TokenIssuer.

## Part 3 — Human-gated minting (HI-CTRL integrity — critical)

DecisionRecords are minted ONLY by a human CLI action:
  snodo adjudicate <session_id> <task_id> <validator_id> --decision proceed|halt --justification "..."
The orchestrator (an LLM) MUST NOT mint DecisionRecords autonomously. It SURFACES the
escalation (from the halt payload) and PROPOSES options; the human runs adjudicate. Do NOT
expose adjudicate as an orchestrator-callable MCP tool that self-resolves — an LLM minting
its own override is the same self-subversion class as the protocol-rewrite incident. HI-CTRL
must be genuinely human or the mechanism is meaningless. (The existing orchestrator-callable
resolve_disagreement MCP path must be removed or restricted to non-minting; surfacing the
escalation is fine, deciding is not.)

## Part 4 — Policy-layer consultation (the override mechanism)

In engine/policy.py the order becomes: count → if error>0 HALT → if blocker>0 HALT →
[NEW: consult DecisionRecords for warns] → policy dispatch. A warn with a valid matching
DecisionRecord(decision=proceed) is reclassified as resolved (not counted as a warn).
Because the blocker/error HALT runs FIRST, no DecisionRecord can ever override a blocker —
INV3 holds by construction.

Validators are NOT changed: they keep reporting honestly (the warn is recorded). Resolution
happens at aggregation. Both the validator's concern and the human's override are on the
audit trail.

Match key (v1): task_ref + validator_id. LIMITATION (state it): coarse — it auto-resolves
any future warn from that validator on that task, even a different concern. Store
adjudicated_justification so drift is auditable. Concern fingerprinting (hash of failed
criteria) is v2 hardening, deferred because validator output today is only
(validator_id, severity, justification) with non-deterministic text.

## Part 5 — Persistence + consumption (fix the dance)

DecisionRecords persist in the session, so a fresh re-dispatch process consults them (no
in-process resume needed). _governance_node loads the task's DecisionRecords and passes
them to the policy evaluator. Remove the old single-use resolution_override consumption.
Decisions are NOT single-use — a re-dispatched task with the same adjudicated concern
proceeds without re-adjudication.

## Constraints

- INV3 by construction: blocker/error HALT before any DecisionRecord consultation; the old
  override path is removed. No code path lets a decision override a blocker.
- HI-CTRL integrity: decisions minted only by human CLI action, never autonomously by the
  orchestrator.
- Unforgeable: signed JWT, same infra/secret as validation tokens (INV1).
- Audited: decision_record_issued / decision_record_verified, hash-chained (INV4).
- Validators unchanged; resolution is at the policy layer.
- DecisionRecords are persistent/durable, not short-TTL single-use.

## Acceptance

- 1-pass/1-warn escalation: human runs `snodo adjudicate <s> <t> security --decision proceed
  --justification "..."`, re-dispatches, the task proceeds — across a NEW process, no
  resolve-then-re-dispatch dance.
- A blocker is NEVER overridable: adjudicate --decision proceed on a task with a blocker does
  NOT allow execution (HALT before consultation). INV3 holds.
- The orchestrator cannot mint a DecisionRecord autonomously (no self-resolving MCP tool).
- DecisionRecords are signed, verifiable, and audited (issued + consulted events in the chain).

## Tests

- DecisionRecord issue/verify: signature valid, tamper detected, task binding enforced;
  issuing with adjudicated_severity="blocker"/"error" is rejected.
- Policy: warn + valid decision(proceed) → resolved; warn without decision → HALT under
  unanimous; blocker + decision(proceed) → still HALT (INV3); error + decision → still HALT.
- Persistence: decision written to session, consulted on a fresh-process re-dispatch (same
  task) → proceeds.
- INV3 regression: the removed resolution_override path no longer bypasses the token gate.
- Audit: decision_record_issued + consultation events appear hash-chained.
- Human-gated: adjudicate is a CLI command and is NOT registered as an orchestrator-callable
  self-resolving MCP tool.
- Existing policy / resolve / loop suites pass or are updated for the new path.
