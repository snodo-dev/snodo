# ADR 044 — Intake proposes criteria from decision records; the operator accepts

## Status

Accepted

## Context

`snodo survey` derives the extension of governance from code — which modules
exist, how each is verified, where decisions live — and derives none of the
protocol's normative content, on the principle that the normative part is prose
a human wrote. Surveying one real governed repository showed the principle was
too broad. Eight of its eighteen decision records stated, in plain words, the
very criteria the protocol encoded by hand: that `org_id` is derived
server-side from a key lookup, that every query carries a tenant filter, that
one function is the authorisation decision point. A human had read those
records and typed the criteria in. The boundary was never "a human must author
the normative part"; it was that intake read code while the records intake
never opened held the norms.

## Decision

`snodo intake` reads a governed repository's decision records and proposes the
rules they state as validator criteria. Each proposal cites the record it came
from, and the operator accepts or rejects each one individually. The protocol
is written only from the accepted set, and a run that accepts nothing writes
nothing.

What makes a sentence proposable is section membership, not semantics: a rule
lives in the record's **Decision** section. **Context** is background,
**Consequences** are effects a reader can observe rather than rules anyone can
violate, **Alternatives considered** is the road not taken, and **Status** is
metadata about the record. None of them is proposed, and a record with no
Decision section proposes nothing. The pass does not judge whether a decision
sentence *ought* to be a criterion — that is the operator's question, answered
one proposal at a time — but it does judge attribution: a proposal without its
record is never built, and the citation is resolved against the repository
with the same discipline a boundary judge already applies to source files.

Intake is a sibling of survey, not a flag on it. Survey's contract is that it
writes nothing; making that contract conditional would weaken it for every
caller, including those that never pass the flag. Intake's contract is the
opposite and its own: it writes, but only what the operator accepts, one
proposal at a time, and never a criterion without the record it came from.

## Consequences

- A governed repository's own records become a source of protocol criteria
  without a human retyping them, while acceptance stays human.
- `snodo intake --json` reports proposals and writes nothing, so a machine can
  inspect the offer without accepting it; `--accept-all` and `--reject-all`
  are the explicit non-interactive forms of the gate.
- Accepted criteria are appended to the protocol's architecture validator by
  default; `--validator <id>` names another. The protocol is re-verified after
  the append but before the write, so an update that would not compile is not
  written.
- A record that states a consequence or a rejected alternative is not a source
  of criteria, which means some true, well-written records are not read at
  all. That is deliberate: a proposal the operator has to think hard to reject
  costs more than it saves.

## Alternatives considered

- **An agent reads the records and writes the criteria directly.** Rejected:
  the normative decision is the operator's, and a model that both proposes and
  writes removes the gate the whole feature exists to keep.
- **Propose from every section, letting the operator reject the noise.**
  Rejected: a consequence and a rejected alternative are not rules, and
  offering them would ask the operator to reject true sentences — the cost
  this decision names.
- **Make it a `snodo survey --propose` flag.** Rejected: survey's "writes
  nothing" contract would become conditional, and a caller that only wanted a
  report would inherit a write path it never asked for.
