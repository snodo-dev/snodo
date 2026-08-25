# ADR 023 — Spec-authoring receives only spec-quality critique

## Status
Accepted

## Context
`engine/nodes/governance.py` rewrites a task spec when pre-execute validation
escalates on a warn, bounded to two attempts. The critique handed to the author
was every non-passing validator's justification, not only the spec validator's.

Observed loop: `architecture` blocked on a stale criterion; its justification
went into the rewrite; the author dutifully added a sentence about the thing it
had blocked on; `architecture` then read that sentence and blocked on the same
criterion for the same reason. The recovery loop wrote its own next violation.

A spec validator's critique is about the **wording** (intent, constraints,
scope). Every other validator's critique is about the **work**. Laundering the
second into the spec changes what the task says it wants — not a rewrite, a
redefinition. It also lets the loop converge on its own objection: each rewrite
incorporates the objection, the objecting validator reads the incorporation and
re-objects, and the two-attempt bound burns on a self-inflicted disagreement.

## Decision

1. **Only spec-quality critique reaches the author.** A `Validator` gains a
   `judges_spec` boolean field. The shipped spec validators — `meta-spec`
   (solo/team/2+n/greenfield) and `spec-manners` (intent) — are marked
   `judges_spec: true`. The escalation site in the live `_validate_node`
   (loop.py) builds `spec_critique` from only those validators, and
   `_spec_authoring_reentry` filters again defensively so a stale non-spec entry
   can never reach the prompt.

2. **A non-spec objection must not silently reshape the spec.** If the only
   escalation comes from non-spec validators (e.g. `architecture` or `security`
   warn), there is nothing to author from, so the task escalates normally
   (`halt_type: "escalated"`) instead of running a pointless rewrite. A non-spec
   **blocker** already halts before escalation and is unchanged (INV3).

3. **The authored spec's provenance is visible.** `_spec_authoring_reentry`
   records `metadata["spec_authoring"] = {attempt, triggered_by, original,
   authored}`, and `_build_halt_payload` includes it as `spec_authoring`. The
   halt payload now shows that a spec was authored, at which attempt, and from
   what, instead of surfacing a rewritten spec with no visible origin.

## How `judges_spec` is identified — and what it misclassifies

Chosen mechanism: an **explicit marker on the validator** (`judges_spec`),
rather than inferring from `validator_type` or from the criteria text. Inferring
from type is wrong because `meta-spec` has `validator_type: "architecture"` — the
same type as the architecture validator that must *not* feed the author.
Inferring from criteria text is a heuristic that would misclassify.

What it misclassifies: a protocol that ships a bespoke spec validator without
setting `judges_spec` gets no authoring (the validator's warn escalates
normally). A protocol that wrongly marks a work validator as `judges_spec`
reintroduces the laundering defect. Both are configuration errors, detectable in
review, and both fail toward the safe direction (no authoring) in the first
case. Unknown validators default to `judges_spec: false`.

## Constraints preserved
- The two-attempt bound is unchanged.
- The post-validation recovery path is untouched (rebuilt separately in ADR 021
  as a different function).

## Consequences
- Shipped protocol templates and their golden snapshots gain the `judges_spec`
  field.
- `Validator` model dumps include `judges_spec`; the machine interface
  (ADR 022) schema is unaffected because this field is additive.
- The halt payload grows a `spec_authoring` provenance block.

## Alternatives considered
- **Infer spec-judging from `validator_type`**: rejected — `meta-spec` shares
  `architecture` as its type, so type cannot distinguish them.
- **Prepend a marker to criteria text** (e.g. "SPEC:" prefix): rejected —
  brittle, couples the marker to prose, and is trivially misread by an LLM.
- **Carry non-spec critique to the author as context but forbid writing it into
  the spec**: rejected — the prompt instruction "do not incorporate work
  critique" is advisory, and the observed defect is exactly an advisory
  instruction being ignored.
