# ADR 052 — A validator's fail-safe on unreachable verification is a declared, per-validator opt-in

## Status

Accepted.

## Context

An acceptance validator in production at droptrack.io judged a criterion that
could not be verified at runtime as "uncheckable, therefore never a finding."
It silently passed the task instead of refusing. That is the wrong direction
for a validator: when a criterion demands verification that the validator
cannot perform, the absence of a tool is not evidence that the criterion is
satisfied.

The failure generalises beyond the acceptance validator. Validators have
different purposes and deliberately different read-only tool surfaces. The
existing `ValidatorConfig.tools: List[str]` field is each validator's declared
read-only tool allowlist; an empty list means no tool access. The decision
needed is therefore not a second capability taxonomy, but whether a given
validator opts into fail-safe handling of verification it cannot reach.

## Decision

`ValidatorConfig` gains `check_tool_access: bool`, defaulting to `False`.
This is a per-validator declaration, not a global mode. The separate
implementation ticket that adds the field also wires protocol entries that
want this behaviour to set it to `True`.

When `check_tool_access` is enabled, the validator must refuse rather than
silently pass a criterion that demands verification by a tool outside its
own declared `tools` list. The validator's existing allowlist remains the
source of truth for what it can use; this decision does not invent parallel
capability tags or infer access from a different registry.

The refusal is a `blocker`. It is never overridable by a human decision and
sends the work back, using the existing blocker semantics. The protocol
guarantee is narrow and firm: an enabled validator never silently passes a
criterion it cannot verify.

Whether "no tool for this" means that no validator in the whole pool can
verify the criterion, or that another validator in the same wave already
can, remains an open implementation detail for the implementing ticket. It
is not a protocol guarantee established by this ADR.

## Consequences

Validators that opt in fail closed on verification they cannot perform,
preventing an unavailable runtime check from becoming an accidental pass.
Validators that do not opt in retain their current behaviour, allowing
incremental adoption rather than changing every validator at once.

The existing `tools` declaration carries both the access contract and the
boundary against which the check is made. A validator cannot claim access to
a tool merely because another validator has it, and no separate capability
surface has to be kept in sync.

The blocker remedy is broader here than the current transport wording
"fix the code and re-validate." The task may need to be fixed instead: the
specification may need to be made smaller or more detailed, or the task may
need a reconnaissance run before validation. A follow-up ticket updates the
literal transport text; this ADR records the intended meaning.

## Alternatives

A global always-on flag was rejected. It would be too disruptive and offers
no incremental adoption path for validators whose protocols are not yet
ready to declare this contract.

A new capability-tag taxonomy separate from `tools` was rejected. It would
duplicate the existing allowlist and create a redundant surface for declaring
what a validator can inspect.

Routing this refusal through `validator_error` was rejected. That halt means
the validator malfunctioned or failed to produce a verdict; here the
validator correctly declines to judge a criterion it cannot verify. Using
`validator_error` would invert the meaning of the existing halt.
