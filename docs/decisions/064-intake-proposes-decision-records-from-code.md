# ADR 064 — Intake proposes decision records from code

## Status

Accepted

## Context

ADR [044](044-intake-proposes-criteria.md) made intake a sibling of survey, not a
flag on it: intake reads existing decision records and proposes validator
criteria, while survey remains read-only. It rejected an agent writing criteria
directly because the normative decision belongs to the operator. A complementary
need is to find recurring code patterns that no decision record states, and
propose records for an operator to consider. This direction belongs under
`snodo intake`, where proposals are already accepted or rejected one at a time,
rather than adding another top-level command.

## Decision

`snodo intake --generate-adr` selects the code-to-record direction; without the
flag, intake keeps its existing records-to-criteria direction. This amends how
ADR 044's “sibling of survey” reasoning applies; it does not change survey.

The model reads code and proposes a decision record only for a pattern found in
at least three places with no counterexample found. Every proposal cites the
files showing the pattern. The operator accepts or rejects each proposal
individually. Acceptance writes a decision record with Status **Proposed**; a
human must still decide whether it becomes Accepted. Rejection writes nothing.
“Proposed” is a status in a decision-record file, not a state in snodo's state
machine. The model is the first entry of `llm.recon.models`, or the configured
default when that list is empty.

The MCP intake tool remains read-only and does not gain this flag. If the
repository has no decision-records folder, intake gives a plain explanation of
how to start one and creates nothing.

## Consequences

- Intake can surface undocumented, repeated code decisions without granting the
  model authority to make them.
- File citations and the three-place threshold make each proposal inspectable;
  the operator remains the gate for every write.
- An accepted record is explicitly Proposed, preserving human review before it
  is treated as Accepted.

## Alternatives considered

- **Add a separate top-level command.** Rejected: intake already provides the
  operator's one-at-a-time proposal gate, and another command is unnecessary.
- **Let the model write records directly.** Rejected for the reason in ADR 044:
  the normative decision is the operator's, and direct writing removes that
  gate.
