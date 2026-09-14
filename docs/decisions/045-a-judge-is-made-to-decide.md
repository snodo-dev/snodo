# ADR 045 — A judge is made to decide; a non-verdict is an error

## Status

Accepted. Supersedes [042](042-abstention-rejudges-not-recovers.md) and
[043](043-halt-payload-attempt-history.md).

## Context

A validator returns a verdict. There is no fourth thing it can return.

Between #252 and #277 the engine grew a fourth: the *abstention*, a validator
result with `severity=None` plus an `abstention_reason`, `examined`,
`unexamined_tools` and `last_words`, a protocol `abstention_policy`, two
canonical halt types (`abstention_exhausted`, `abstention_stalled`), a signed
adjudication path for a verdict nobody gave, an in-place re-judge loop
(ADR 042), and an attempt-history payload that counted non-verdicts
(ADR 043). The state was branched on in twenty-one places across six packages.

#252's concern was correct: a non-verdict must not be reported as a pass. But
`severity=None` was the wrong remedy. `error` already existed and already
fails closed; representing "no verdict" as a fourth severity invented a state
the engine had no reason to carry, and every later change attached to it.

Observed on a real project: five consecutive runs of one task where the
architecture judge read fourteen files, answered in prose, was asked for a
verdict, and went back to reading. Twelve hours went to model swaps, protocol
edits and ticket rewrites, none of which touched the cause: the loop offered
every read tool on every turn, including the turn after it asked the judge to
stop and decide. The judge was not disobeying an instruction; it was choosing
from a menu the loop kept handing it.

## Decision

**1. A judge is made to decide.**

At the boundary of its budget a judge is asked for a verdict and given no way
to keep reading. The final turn offers `submit_verdict` alone; the read tools
are withdrawn. A verdict reached on incomplete reading is a real verdict:
`warn` exists for exactly that, and a `warn` escalating to a human is the flow
working, not failing.

**2. A judge that still returns nothing is an error.**

It takes the path errors already take — fail-closed. An engine that cannot get
a verdict out of its quorum must not proceed. Nothing new is added to express
this: `error=True` with severity `blocker`, the same as any other operational
fault. The record of what a failing judge examined travels on the error result
(`examined`), so a human can still see how far the inspection got.

**3. The invention is removed.**

`severity` is required on `ValidatorResult`. Removed with it: the abstention
fields, `abstention_policy`, the two abstention halt types, the re-judge path,
the adjudicated-abstainer arithmetic in the policy evaluator, the
abstention branches in the halt payload and the CLI, and the `non_verdicts`
count in the attempt summary. Where a removal lost something useful — the
record of what a failing judge examined — it was kept on the error result
rather than reintroduced under another name.

**4. ADRs 042 and 043 are superseded, not deleted.**

Their reasoning is preserved here as the record of a remedy that was wrong.

## Why the earlier reasoning was wrong

ADR 042 argued that an abstention should not route to recovery because there
is no fault for a coder to fix, and reached for a re-judge loop. The premise
was right and the conclusion was a workaround: the correct fix was to stop the
judge from reaching "no verdict" at all. A loop that offers read tools on the
turn it asks for a decision is a loop that will keep getting prose; the
re-judge was spending judge calls to route around a defect in the loop.

ADR 043 then recorded non-verdicts as a durable category in the halt payload —
`attempts.non_verdicts`, the `abstained` outcome — on the strength of 042's
state. With the state gone, the category describes nothing. The useful part of
043 survives: the payload still records every attempt as `{attempt, outcome}`
over `passed` / `warned` / `blocked` / `error`, so a hard-won recovery is still
distinguishable from a first-time pass.

The through-line: both records treated the symptom (the judge did not decide)
as a thing to be handled. The cause was the tool menu on the final turn, and
the honest expression of "the judge produced nothing" is an error.

## Consequences

- `ValidatorResult.severity` is non-optional. Every construction site supplies
  a verdict.
- A judge that never calls `submit_verdict` produces `error=True, severity=
  "blocker"` and halts as `validator_error`; the task fails closed.
- The final turn's request to the model carries `submit_verdict` alone.
- The halt payload's `attempts` has no `non_verdicts`; its outcomes are
  `passed`, `warned`, `blocked`, `error`.
- A no-verdict judge is counted as a blocker in `blocker_validators` and in
  the policy counts; there is no separate list or count.
- `abstain` no longer appears anywhere in the packages except this record.

## Alternatives

- **Keep `severity=None` but stop offering read tools on the final turn.**
  Rejected: it leaves a fourth state in the type and every branch it grew, to
  describe a case that is an operational error. The final-turn fix and the
  removal are the same decision seen from two ends.
- **Force the verdict by parsing the judge's prose into a severity.** Rejected
  outright: forcing the call is not the same as inventing its content, and
  nothing may derive a finding from untrusted text. A judge that will not call
  the tool is an error, not a verdict to be guessed.
- **Add a configuration key to choose between blocking and non-blocking.**
  Rejected: the intent forbids a key that chooses between behaviours, and once
  a non-verdict is an error there is no second behaviour to choose.
