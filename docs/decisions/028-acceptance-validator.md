# ADR 028 — Post-execute acceptance validator judges artifacts against the task's acceptance criteria

## Status

Accepted

## Context

A task specifying three acceptance criteria completed successfully and
auto-merged with two of them unmet: no test covering the new feature, and no
ADR recording a decision the code now contradicts. Every validator passed.

The cause is structural. `quality` is the only post-execute validator and all
it does is run the project's test command. That command passes when new code
arrives uncovered, and it has no knowledge of what the task said "done" meant.
The read-only judges all run pre-execute, against the proposal — by the time
artifacts exist, nothing reads the spec again.

So the pipeline verifies that the repository still works. It does not verify
that the task was carried out. Those diverge exactly when a coder does part of
the job, which is the common case.

The pieces already exist: the spec is in loop state, artifacts are enumerated,
and validators can hold tools to read the tree. The fix is a validator, not a
new subsystem.

## Decision

1. **A new `acceptance` validator type, post-execute.** It judges the produced
   artifacts against the acceptance criteria in the task spec. It reuses the
   LLMValidator tool loop unchanged; only the judge prompt differs. It is
   registered in the validator registry like every other validator and shipped
   in the `solo`, `team`, `2+n`, and `greenfield` templates.

2. **Warn, not blocker.** "You forgot the test" is exactly the kind of fault a
   coder can fix given the feedback, so a miss routes to recovery rather than a
   hard halt. The validator is shipped with `severity_cap: warn` so a miss can
   never hard-block even if a protocol forgets the cap.

3. **"Unmet" is distinct from "uncheckable".** A criterion that cannot be
   verified from the tree (device behaviour, human judgement, a decision only a
   human can make) is reported as uncheckable and does NOT block good work. The
   judge is told to return `pass` for uncheckable criteria and to say so in the
   justification; only criteria that are verifiable from the tree and
   demonstrably unmet produce a warn. A validator that cannot tell "unmet"
   from "uncheckable" would block good work.

4. **Not a second `quality`.** It judges completeness against the spec, not
   correctness of the code. It never runs commands; it reads the tree. The
   prompt explicitly forbids treating the test command as a criterion.

5. **Artifacts are threaded to the validator context.** `run_validators` gains
   an `artifacts` parameter (post-execute only; empty for pre-execute), carried
   on `ValidatorContext.artifacts`. The post-validate nodes pass
   `loop_state.artifacts`. The acceptance validator lists them in its prompt so
   the judge knows what was produced.

## Consequences

- A task whose acceptance criteria are unmet now fails post-execute with a
  warn, routing to recovery, instead of auto-merging.
- The acceptance validator is opt-in per protocol (a validator entry in the
  mode's validator list), like every other validator.
- The `solo`, `team`, `2+n`, and `greenfield` templates ship it; their golden
  snapshots and well-formedness tests are updated.
- The mock completion function already answers `submit_verdict` tool loops, so
  `--mock` runs stay hermetic with the new validator present.

## Alternatives considered

- **A hard blocker on any unmet criterion:** rejected — "you forgot the test"
  is a fixable fault; recovery is the right outcome.
- **A deterministic predicate (e.g. "tests exist for modified files"):**
  rejected — acceptance criteria are free text in the spec; only an LLM judge
  can map them to the tree. The predicate framework remains available for
  criteria that are mechanically checkable.
- **Extending `quality` to read the spec:** rejected — `quality` runs the test
  suite; mixing completeness judgement into it would blur two different
  questions and make the test command a proxy for "done".
