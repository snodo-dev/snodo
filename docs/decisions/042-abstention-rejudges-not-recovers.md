# ADR 042 — An abstention re-judges the judge, not the coder

## Status

Superseded by [045](045-a-judge-is-made-to-decide.md). The abstention state
this record handles was removed: a judge is now made to decide at the boundary
of its budget, and a judge that still returns nothing is an error. See 045 for
why the remedy here was wrong.

## Context

A post-execute abstention is a judge reporting that it did not reach a verdict
(`severity is None`), not a finding about the code. The engine's recovery
machinery exists to change code: it synthesises a recovery spec from the failures
and dispatches a coder against it. Routing an abstention there therefore
re-runs the coder at work no judge faulted and hands it a recovery spec
describing a failure that was never diagnosed.

Observed on a real project: a task completed, its acceptance judge abstained,
and the engine spawned three recovery attempts over thirty-five minutes. Each
re-ran the pre-execute quorum, dispatched the coder, and logged "git add staged
nothing (coder produced no changes)" — the work had been correct and committed
since the first attempt. On the fourth acceptance run the judge passed on that
same untouched code. Four coder dispatches, one of which mattered.

The stall check did not catch it. It compares verdict signatures across
attempts, and the three abstentions carried three different prose
justifications, so they never looked identical even though nothing about the
situation had changed.

## Decision

**1. An abstention is retried in place, before the recovery machinery.**

When the post-execute policy halts or escalates *solely* because a judge
abstained — no `error`, no `warn`, no `blocker` — the engine re-runs the
abstaining judges on the unchanged work instead of spawning a recovery subtask.
The retry lives in the post-execute validation node, not in `_spawn_recovery_subtask`,
precisely because recovery is the wrong tool: it exists to change code, and an
abstention names nothing to change. No task branch is created for a missing
verdict.

**2. The retry is bounded by `max_recovery_depth`.**

`max_recovery_depth` is the recovery ceiling the operator already sets, per
mode. A protocol that permits no recovery (`max_recovery_depth: 0`) permits no
re-judging. This reuses the existing bound rather than adding a second knob with
the same meaning; the number an operator tunes to say "how many attempts may
this cost" is the same number.

**3. A repeated abstention is a stall, whatever prose accompanies it.**

`_verdict_signature` canonicalises an abstention to a single marker with no
justification. Two abstentions by the same judge describe the same stall, so the
signature compares them equal and re-judging stops instead of spending another
judge call. This is the same signature the recovery stall check uses; there is
one implementation so the two cannot disagree about what "nothing changed" means.

**4. The terminal outcome is named and adjudicated, not retried with a coder.**

When the retry budget is spent (or a repeated abstention is detected) the task
halts as `abstention_exhausted` (or `abstention_stalled`), both canonical
`blocker` per ADR 015. They remain adjudicable with `snodo authorize <task_id>`,
and the CLI's halt follow-up offers that rather than `snodo run --retry`, which
would re-dispatch a coder at work no judge faulted.

**5. `abstention_policy` is unchanged.**

It still decides what an abstention means: `"blocking"` halts, `"non_blocking"`
excludes the abstention from the counts. The re-judge only changes what a halt
or escalation *caused solely by abstentions* does next. An abstention is never
converted into a pass.

## Consequences

- An abstention on committed work costs judge calls, not coder dispatches, and
  does not disturb the branch.
- A judge that cannot decide after the bounded retries stops the run for a human
  rather than looping — the same fail-closed posture as before, reached without
  manufacturing a code failure.
- The two new halt types are additions to the raw halt vocabulary; both
  canonicalise to `blocker`, so the five-outcome contract (ADR 015) is untouched.
- `abstention_exhausted` and `abstention_stalled` name no code fix target: the
  halt hint points at the judge and its policy, not the produced code.

## Alternatives

- **Retry the whole task through the recovery machinery, but skip the coder.**
  Rejected: a recovery subtask exists to carry a coder change; a subtask that
  never dispatches a coder is the in-place retry wearing recovery's clothing,
  and would still create a task branch for a missing verdict.
- **Add a separate `max_abstention_retries` config key.** Rejected: it is the
  same quantity as `max_recovery_depth` under a different name, and two knobs an
  operator must keep in sync drift.
- **Treat a repeated abstention as a fresh question each time (compare full
  prose).** Rejected: that is the defect — three excuses disguised one
  unchanging situation and spent three coder dispatches.
- **Convert the abstention to a pass after N tries.** Rejected outright: it
  silently downgrades no-verdict to approval, which ADR 015 and #252 exist to
  prevent.
