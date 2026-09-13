# ADR 043 — A halt payload records the attempts that produced the result

## Status

Accepted.

## Context

The halt payload is the machine-readable account of what happened to a task: an
orchestrator, the CLI, and snodo cloud read it. It carried the final validator
results and the final policy decision, and nothing about how the task reached
them. A task that passed first time and a task that passed on the fourth attempt
after three silent judges produced payloads a reader could not distinguish.

Observed on a real project: a task took four attempts over forty-five minutes,
three acceptance judgements abstained, and three coder dispatches produced no
changes. The payload read `status: completed`, unanimous pass,
`abstain_count: 0`. Every failure was absent; the only surviving trace was a
sentence buried in another validator's prose.

The information was not missing. `Task.prior_failures` already accumulates
failures across recovery attempts, tagged with 1-based attempt numbers and
including abstentions, because it is what the recovery spec is built from.
`LoopState.abstention_retries` already counts the in-place abstention re-judges
introduced by ADR 042. Both reached the recovery spec and the audit events and
stopped there.

## Decision

**1. The halt payload carries an `attempts` summary.**

`attempts.total` is the number of judged attempts the task took;
`attempts.non_verdicts` is how many of them ended without a verdict;
`attempts.coder_dispatches` is how many coder runs the chain cost; and
`attempts.history` enumerates each attempt as `{attempt, outcome}` with a
canonical outcome (`passed`, `warned`, `blocked`, `abstained`, `error`). The
tokens are the same across projects, unlike the prose in `validator_results`,
so payloads aggregate: a consumer can count non-verdicts and coder dispatches
without reading a judge's words.

**2. An attempt is one judged pass over a code state.**

The root is attempt 1, each recovery subtask is the next attempt, and each
in-place abstention re-judge is an attempt of its own — it re-runs a judge on
unchanged work and dispatches no coder. The two invisible-to-the-results
categories, prior recovery attempts and in-place re-judges, are exactly what
the summary recovers.

**3. The counts are exact; the enumerated history is bounded.**

`total`, `non_verdicts` and `coder_dispatches` are counts over the whole chain.
`history` keeps the most recent `_MAX_ATTEMPT_HISTORY` entries and reports the
number omitted in `attempts.omitted`, so a pathological run cannot grow the
payload without bound.

**4. Halt types and final results are untouched.**

No halt type is added or changed, and the final results stay in
`validator_results` / `policy_decision`. The summary is the history that
precedes them, not a second name for them.

## Consequences

- A clean pass reports one attempt and zero non-verdicts; a hard-won pass
  reports the chain, so the two are distinguishable from the payload alone.
- The cost of a completed task — judge retries and coder dispatches — is
  answerable from payloads, which is what the cloud and any dashboard count.
- The summary is derived, not journaled: it is reconstructed from
  `prior_failures` and `abstention_retries`. A prior attempt's *internal*
  re-judges are not individually retained (only the attempt's final verdict
  is), so the history is a faithful attempt-level account rather than a
  judge-call-level one.

## Alternatives

- **Add a mutable attempt journal to `Task`, appended at each judge run.**
  Rejected: it would carry the same information the engine already accumulates,
  in a second place that can drift, and widen the core model and the closure
  serde for no structural gain.
- **Restate the final results under an `attempts.final` key.** Rejected: the
  payload already carries `validator_results`; the summary is the preceding
  history, and a second copy is the drift this ADR avoids.
- **Emit the full history unbounded.** Rejected: the payload is read by
  orchestrators and the cloud, and a long chain must not inflate it; the exact
  counts preserve the aggregation while the list stays bounded.
