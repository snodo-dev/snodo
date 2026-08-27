# The coder seam: why adapters drift, and what the contract should be

Status: analysis, not yet a decision record
Date: 2026-08-25
Related: ADR 026 (`.snodo/` protected from agent mutation), ADR 027 (in-place coder mutation halt)

---

## 1. The design intent, stated plainly

snodo is not a coder and should not try to be one. Code generation is a fast-moving,
heavily-contested field with excellent dedicated tools; snodo's contribution is the
layer above — protocol compilation, capability bounded by the active mode, a
hash-chained audit trail, bounded recovery, and a halt classification an operator can
act on.

That gives the coder seam a specific job: **let an expert CLI do the generating, and
still hold the protocol's guarantees over the result.**

The built-in `litellm` coder exists so that snodo works with nothing else installed.
It is a convenience, not the product. If a CLI is available, it should be preferred.

This intent is sound, and the rest of this document assumes it. The problems below
are not arguments against delegation — they are the cost of having built the seam
implicitly rather than declaring it.

---

## 2. What the seam actually is today

There is no declared contract. An adapter's obligations are discovered by reading
the engine, and they are expressed three different ways:

**By ABC.** `Coder.implement(spec: TaskSpec) -> CodeArtifact`. This is the only part
that is explicit, and it is the smallest part.

**By opt-out flags.** Two booleans change engine behaviour:

| Flag | Effect on the engine |
|---|---|
| `skip_workspace_write` | The executor does not write the returned artifacts through `WorkspaceMCP` |
| `skip_engine_commit` | The executor does not stage or commit |

**By duck typing.** The engine reaches into the adapter with `hasattr` guards and
sets attributes if they happen to exist:

```python
if hasattr(coder, "progress_callback"):
    coder.progress_callback = self._progress          # loop.py, executor.py
if hasattr(coder, "_job_id"):
    coder._job_id = job_id                            # executor.py
```

This is the whole mechanism. Nothing declares what an adapter must provide, nothing
detects what it does not, and nothing fails when the answer changes.

---

## 3. Why divergence is structural, not accidental

Three forces, and all three are properties of the design rather than of anyone's
diligence.

### 3.1 Capability negotiation by `hasattr` makes drift undetectable by construction

When the engine gained per-turn progress reporting (#51), it started setting
`coder.progress_callback` behind a `hasattr` guard. The opencode adapters do not
define that attribute, so the guard silently skips them. No error, no warning, no
failing test, no log line. The feature simply does not exist on two of four adapters
and nothing in the system knows.

That is the general case: **any capability the engine offers optionally is a
capability some adapter will silently lack**, and the absence is indistinguishable
from the feature not existing at all. Divergence cannot be observed, so it
accumulates.

### 3.2 Opting out of engine behaviour carries no corresponding obligation

`skip_engine_commit = True` says "do not commit for me." It does not say "therefore I
will commit." One adapter took on the obligation; the other did not; nothing noticed
(see §4).

This is the design flaw in a sentence: **the flags are permissions to opt out of a
mechanism, with no transfer of the responsibility that mechanism discharged.**

### 3.3 Features land where the exercise happens

`litellm.py` was modified four times in the last two days (#39, #34, #51, #53). The
opencode adapters were touched twice in the same window, and both times as part of a
sweep that had to touch every adapter — never on their own merits. Their last
adapter-specific commits are 2026-06-30 and 2026-07-28; `opencode_container.py` has
not been touched on its own merits since 2026-06-26.

Their tests tell the same story: `test_opencode_adapter.py` (23 tests) and
`test_opencode_cli_adapter.py` (21 tests) are reasonable coverage, last updated
2026-06-30, and were not touched by #51, #52 or #53. Meanwhile `.snodo/` blocker
behaviour is tested only through synthetic `InPlaceCoderAdapter` subclasses, never
against the real adapters.

So the path that is used gets developed and tested, and the path that is not used
rots — while continuing to pass a suite that was written before the divergence began.

---

## 4. What the drift actually cost: two channels for "what changed"

There are two independent readers of the change an adapter produced, and only one of
them is fed by every adapter.

**Channel A — `CodeArtifact`.** The adapter's return value. Populated by
`submit_files` arguments (litellm), or by a git working-tree diff plus untracked files
(both opencode adapters), with `GET /session/{id}/diff` as a fallback on the container
path. **All three adapters populate this, and it works without a commit.**

**Channel B — `git diff HEAD~1..HEAD`.** Post-execute validators do not read the
artifacts to see the change. They are handed a diff:

```python
change_diff = git.diff_between_refs("HEAD~1", "HEAD")   # llm_validator.py:235
...
"## Code Change (HEAD~1..HEAD)\n",                       # llm_validator.py:414
```

and the acceptance validator does the same (`acceptance.py:94`).

Who fills Channel B:

| Adapter | Channel A | Channel B |
|---|---|---|
| `LiteLLMAdapter` | `submit_files` arguments | executor commits — **yes** |
| `OpenCodeCLIAdapter` | working-tree diff | adapter commits itself — **yes** |
| `OpenCodeAdapter` (container) | working-tree diff, `/diff` fallback | **nobody commits — no** |

### The failure is worse than an empty diff

On the container path `HEAD` never moves, so `HEAD~1..HEAD` does not resolve to
nothing — it resolves to **the previous commit**. The validators are handed a valid
diff of a different change, labelled "## Code Change" for this task, and they review
it confidently and pass.

This is not hypothetical. The CLI adapter's own comment records it happening:

> "opencode writes files in place and never commits; without this the committed diff
> is empty and any post-execute reviewer is blind (this is what silently neutered
> arm-c review in EXP1)."

The fix was made in the CLI adapter and never back-ported.

### Why this one is snodo's problem and the others are not

Progress reporting, truncation detection, turn budgets and context management all sit
on the CLI's side of the line. opencode owns its agent loop; snodo re-implementing
those would undo the delegation that is the whole point.

Channel B is different. It is not about how the change was generated — it is the
protocol's post-execute reviewers being fed the wrong input. Under the stated intent
("enforcement must hold regardless of who generated the change"), a coder path where
validation silently inspects someone else's work is the most serious class of defect
the system can have.

Two smaller items sit on the same side of the line:

- **"Coder produced nothing" is downgraded** from a hard `ExecutionError` to an audit
  note whenever `skip_engine_commit` is set (`executor.py:52-59`). A no-op run fails
  loudly on litellm and continues quietly on opencode.
- **No usage or cost record.** The opencode adapters never touch litellm, so an
  opencode run's spend is entirely absent from the audit trail. Whether that matters
  depends on whether cost is considered part of what snodo attests to — worth
  deciding rather than inheriting.

---

## 5. The contract worth declaring

The obligation is not "commit", and it is not feature parity with `litellm`. State it
in terms of what the protocol needs, and let adapters satisfy it however they like:

> **An adapter must leave the workspace in a state where what changed is
> observable, attributable, and reviewable through the same channel for every
> adapter — however the change was produced.**

Three obligations. Nothing about tokens, turns, models, streaming or progress. An
adapter that shells out to a CLI, calls an API, or writes files itself can satisfy all
three.

Two ways to discharge it, and the second is better:

**(a) Require the commit.** Every in-place adapter commits before returning. Cheap,
matches the CLI adapter, and keeps Channel B as-is. But it leaves two channels, and
the next adapter can forget again.

**(b) Collapse the channels.** Post-execute validators receive the change through the
same path the artifacts came from, so no adapter can starve them. This removes the
class of bug rather than the instance, and it is the same move that made `.snodo/`
detection reliable — put it in the base class, where it cannot be forgotten.

Whichever is chosen, the enforcement should follow ADR 027's pattern: **in
`InPlaceCoderAdapter`, not in each adapter.** That decision has already paid for
itself once — `.snodo/` mutation detection is the single recent capability the
opencode adapters have, precisely because it was implemented one level up.

### Making drift observable

The contract is worth little if nothing checks it. A conformance test parameterised
over *every* registered adapter, asserting the three obligations against a fixture
repository, converts silent divergence into a red test at the branch. Add to it
whenever the engine gains an expectation of adapters.

The complementary change is to stop negotiating capability with `hasattr`. If the
engine needs to hand an adapter a progress sink, a job id or a task id, that belongs
in a declared optional interface with a default implementation on the base class —
then "this adapter does not support X" is a visible fact rather than a silently
skipped line.

---

## 6. Immediate actions

1. **File and fix the container-commit defect.** P1: post-execute validators on
   `opencode/<model>` review the previous commit. Decide (a) or (b) above while
   fixing it. **Done** — ADR 030, `InPlaceCoderAdapter` owns the commit.
2. **Add the adapter conformance test** over every registered adapter.
   **Done** — `tests/coders/test_adapter_conformance.py`.
3. **Decide whether the opencode path is supported or experimental**, and say so in
   the docs. Sitting between the two is how the container adapter came to silently
   blind the reviewers. **Done** — **experimental** (ADR 034); recorded in
   `docs/protocol.md`, `docs/architecture.md`, the `init` output, and the runbook.
4. **Decide whether cost attribution is part of the audit trail.** If it is, the
   opencode paths need to contribute to it; if it is not, say so explicitly so the
   absence is a decision rather than an oversight. **Done** — **cost is not part
   of the attestation.** The audit trail never carries cost, for any coder; token
   and cost data are operational telemetry in per-job `state.json` (`snodo meta`).
   The opencode paths' absence is a documented non-goal (ADR 034, issue #69).
   Whether cost should ever become attestable is a change to the attestation
   contract for all coders, deliberately out of scope here.
