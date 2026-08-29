# snodo — assessment and next wave

Date: 2026-08-27. Written against `main` at `aea1815`. Companion to `docs/HANDOVER.md`.
Every claim below was checked against the tree; where a claim is inference rather than
observation it says so.

---

## Summary

Five findings, in priority order. Three are the same shape as today's wave — a check or a
record that reports success while not doing its job. Two of them are new (not in the
handover), and one of those is the measurement itself.

| # | Finding | Priority | Size |
|---|---------|----------|------|
| A | The acceptance measurement collapses every task into its worktree name | P0 | small |
| B | Six live node methods have stale shadow copies in the mixins they replaced | P0 | small |
| C | The coder seam: nothing enforces that HEAD moved before validators diff it | P1 | medium |
| D | `post_execute` has no WF3 guard; `pre_execute` does | ~~P2~~ decided: closed | — |
| E | Release publishes on a tag with no test gate and no tag/version check | P2 | small |

Two corrections to the handover:

- **"No version, no release, no install story" is overstated.** `pyproject.toml` is at
  `0.6.1`, `README.md` has an Install section, `.github/workflows/release.yml` publishes
  all packages via uv trusted publishing on `v*.*.*` tags, and the CHANGELOG is being kept.
  The real defect is narrower and worse — see finding E.
- **B was not on the list at all**, and it sits directly under finding C. Any fix landed in
  `engine/nodes/validation.py` today is dead on arrival.

---

## A. The acceptance measurement collapses tasks into worktree names — P0

**This is the number the handover says matters, and it is currently not being recorded.**

`.snodo/audit.log` holds 7 `task_merged` events and 7 `human_review_recorded` events.
They resolve to **4 distinct `task_ref` values**: `agent-a`, `agent-b` (×3), `agent-d`,
`agent-e` (×2). `task_ref` is the *worktree branch name*, not a task identity.

`task_report_command` (`snodo/cli/commands/task_cmd.py:444`) builds
`latest_reviews: dict[task_ref -> verdict]`. Three merges of `agent-b` therefore occupy one
key and only the last verdict survives. There are five worktrees, so the denominator is
bounded at five no matter how long the tagging runs. Two weeks of merges will produce a
report of five rows.

Second defect, same function: the denominator counts `task_merged` and
`verification_executed`, but the engine emits `task_complete` — the actual "this task
reports completed" signal — and the report never reads it. So the number being computed is
"of branches I merged, how many did I accept", not "of tasks that reported `completed`, how
many survived unchanged". Those differ precisely in the cases a sceptic cares about: a task
that reported completed and was never merged is invisible.

The audit log is hash-chained, so this is additive only — the 7 existing rows cannot be
rewritten and should be treated as lost.

## B. The node mixins are stale shadow copies — P0

`GraphBuilder` (`engine/loop.py:260`) inherits `GovernanceNodeMixin`, `ValidationNodeMixin`,
`ExecutorMixin`, `SerdeMixin`, `WritebackMixin`, `ContextMixin` — and then redefines six
methods in its own class body:

| method | live (loop.py) | shadowed | identical? |
|---|---|---|---|
| `_governance_node` | 486 | `nodes/governance.py:179` | no — 87 vs 80 lines |
| `_validate_node` | 585 | `nodes/validation.py:39` | no — 121 vs 97 |
| `_execute_node` | 718 | `nodes/validation.py:146` | no — 109 vs 60 |
| `_post_validate_node` | 835 | `nodes/validation.py:214` | no — 108 vs 74 |
| `_route_after_validation` | 1183 | `nodes/validation.py:299` | no — 12 vs 15 |
| `_route_after_post_validation` | 1096 | `nodes/validation.py:317` | yes |

The class body wins on the MRO, so the mixin copies never run. They have diverged — the
live copies are consistently longer, i.e. the shadows are the state of the code at
extraction time and every fix since has gone to `loop.py`. The tests reach these through
`GraphBuilder`, so the suite is green and the dead code is invisible to it.

`ExecutorMixin` is *not* affected: none of its five methods are shadowed, and
`tests/coders/test_adapter_conformance.py` exercises it legitimately.

The hazard is concrete and immediate: finding C's fix touches `_execute_node` and
`_post_validate_node`. An agent told "fix the post-execute diff" will plausibly open
`nodes/validation.py`, land a correct fix, watch its tests pass through `GraphBuilder`
because the fix was needed in `loop.py` and... no. The tests would fail. The failure mode is
the reverse and worse: an agent that writes its canary against `ValidationNodeMixin`
directly gets a green canary over dead code. That is the wave's shape exactly.

Not verified at runtime (no venv reachable from this session). The agent should confirm with
`GraphBuilder._validate_node.__qualname__` before touching anything; it should print
`GraphBuilder._validate_node`.

## C. The coder seam — P1

As the handover has it, with the mechanism nailed down.

`llm_validator.py:316` and the acceptance prompt (`acceptance.py:94`) both read
`git diff HEAD~1..HEAD`. Nothing in the engine establishes that HEAD moved during execute,
so the contract is "every adapter must arrange for HEAD to have moved" and it is enforced
nowhere.

`InPlaceCoderAdapter._commit_changes` (`coders/base.py:158`) is the only thing holding it up
for in-place adapters, and it returns silently on three paths: repo cannot be opened,
`git add` fails, nothing staged. Its own docstring says "non-fatal on failure — the working
tree still holds the change — but the post-execute diff would then be empty". It is worse
than empty: `HEAD~1..HEAD` resolves to the *previous, unrelated* commit and the judges
review that and pass.

`_apply_file_operations` (`nodes/executor.py:44`) already raises `ExecutionError` when the
coder produced no file operations, so "coder did nothing" is caught. The uncaught case is
"coder produced file operations and the commit did not happen" — which is the case that has
occurred once.

The seam is clean. `ValidatorContext` (`validators/context.py`) is built once per pass in
`validators/runner.py:232` and already carries `git_mcp` and `phase`; it needs `base_ref`.
`GitMCP` (`packages/snodo-tools/src/snodo/tools/git.py`) has `diff_between_refs`, `log`,
`show` — but no way to read the current HEAD sha, so that is the one new primitive.

## D. `post_execute` has no WF3 guard — DECIDED: closed, see bottom of file

This is the concrete half of the unresolved `mode`-as-bare-`str` argument, and it can be
settled on evidence rather than taste.

`_validate_node` treats two conditions as halts:

- mode does not resolve → `halt_type="constraint"`, `"Invalid mode: {x}"` (loop.py:597)
- mode resolves but has no `pre_execute` validators → `halt_type="wf3"`, audit
  `wf3_runtime_violation` (loop.py:605)

`_post_validate_node` treats *both* as a benign bypass: `if not current_mode or not
post_validators:` → audit `post_validate_bypassed` with `reason: "no_post_execute_validators"`
→ return, task proceeds to `complete` (loop.py:847-851). A mode authored with zero
post-execute validators has no quality gate at all, and the only trace is an audit note whose
`reason` cannot distinguish "this mode has no gate" from "this mode does not exist".

`resolve_validators` returns `(None, [])` for an unknown mode (`validators/runner.py:36`),
which is why both conditions land in one branch.

**This section is superseded.** The reasoning above stands as a description of the code, but
the conclusion it was heading toward — that absence of a post-execute gate should become a
halt unless explicitly declared — was rejected. See "D — decided" at the bottom of this file.
Kept here because the `pre_execute`/`post_execute` asymmetry is real and the next reader will
find it too.

## E. The release path publishes unchecked — P2

- Tag `v0.6.1` points at commit `d5b2138 release: v0.6.0`.
- `CHANGELOG.md` has `[Unreleased]`, then `[0.6.0]`. There is no `[0.6.1]` section.
- `pyproject.toml` says `version = "0.6.1"`.
- 155 commits between `v0.6.1` and HEAD.
- `.github/workflows/release.yml` runs on `v*.*.*`, then `uv build --all-packages` and
  `uv publish dist/*`. It does not run the test suite, and it does not check that the tag
  matches the version it is about to publish.

So the release process will publish whatever `pyproject.toml` says under whatever tag was
pushed, without running a test, and has already produced one tag that does not describe its
commit. Same shape: a gate that reports success without doing its job.

---

# Agent-worktree prompts

Ready to paste. Each follows the conventions: issue first, stop on duplicate, clean
worktree, three verification commands, canary required, push but do not merge.

---

## Prompt A — measurement identity (do this first)

```
cd ~/Dev/snodo-a

Confirm the worktree is clean and at origin/main before starting.

File an issue first. If an open issue already describes this bug, stop and report
rather than implement.

Issue: `snodo task review` cannot count more than five tasks

`task_report_command` (snodo/cli/commands/task_cmd.py:444) keys review verdicts on
`task_ref`, and the merge path records `task_ref` as the worktree branch name.
.snodo/audit.log currently holds 7 task_merged and 7 human_review_recorded events that
resolve to 4 distinct task_refs — agent-b appears 3 times and only its last verdict
survives into the report. There are five worktrees, so the report's denominator is
permanently bounded at five however long the tagging runs. This is the acceptance
measurement the project exists to produce, and it is not being produced.

Second defect in the same function: the denominator is built from `task_merged` and
`verification_executed` events, but the engine emits `task_complete`. The report
therefore answers "of branches I merged, how many did I accept" rather than "of tasks
that reported completed, how many survived inspection unchanged". A task that reported
completed and was never merged is invisible to it.

Do:

1. Give each merged unit a distinct identity in the audit record. The merge commit sha
   is the natural one. Keep the branch name as a separate human-readable field — do not
   overload task_ref. `human_review_recorded` must reference the same identity as the
   `task_merged` it reviews.
2. Make the report key on that identity, so N merges of one worktree are N rows.
3. Read `task_complete` for the completed-task denominator. If you keep the merge-based
   count too, label the two numbers distinctly in both the text and --json output; they
   answer different questions and conflating them is the bug.
4. The audit log is hash-chained: this is additive only. Do not rewrite the 7 existing
   events. Treat them as lost and say so in the CHANGELOG entry.

Canary — required, and it must fail before your fix:
Record two merges of the same branch with different verdicts, then assert the report
shows two tasks and both verdicts. Against current main this test must show one task.

Verify with:
  uv run pytest tests/ -q -n auto
  uv run ruff check .
  uv run lint-imports
Report all three counts. Do not run e2e.

Commit and push the branch so CI runs. Do not merge.
```

## Prompt B — the shadowed node methods

```
cd ~/Dev/snodo-b

Confirm the worktree is clean and at origin/main before starting.

File an issue first. If an open issue already describes this bug, stop and report
rather than implement.

Issue: six node methods exist twice, and the copies that look canonical are dead

GraphBuilder (snodo/engine/loop.py:260) inherits GovernanceNodeMixin, ValidationNodeMixin,
ExecutorMixin, SerdeMixin, WritebackMixin, ContextMixin — and redefines six of their
methods in its own class body, which wins on the MRO:

  _governance_node             loop.py:486   shadows nodes/governance.py:179
  _validate_node               loop.py:585   shadows nodes/validation.py:39
  _execute_node                loop.py:718   shadows nodes/validation.py:146
  _post_validate_node          loop.py:835   shadows nodes/validation.py:214
  _route_after_validation      loop.py:1183  shadows nodes/validation.py:299
  _route_after_post_validation loop.py:1096  shadows nodes/validation.py:317

Five of the six have diverged; the live copies are longer in every case, so the mixin
copies are the state of the code at extraction time and every fix since has landed only
in loop.py. The suite reaches these through GraphBuilder, so it is green and blind to it.
A future fix landed in nodes/validation.py — with a canary written against the mixin
directly — would pass while production stayed broken.

Start by confirming at runtime: GraphBuilder._validate_node.__qualname__ should print
GraphBuilder._validate_node. Report what it actually prints before changing anything.

Do:

1. Pick one home per method and delete the other copy. Moving loop.py's live bodies into
   the mixins is the direction the extraction intended; keeping them in loop.py and
   deleting the mixin copies is also acceptable. Choose one, apply it to all six, and say
   which you chose and why in the commit message. Do not merge the two versions by hand —
   the loop.py body is the one that has been running.
2. Add the gate: a test that walks GraphBuilder.__mro__ and fails if any method defined in
   GraphBuilder's own __dict__ is also defined in the __dict__ of any of its mixins. This
   is the canary; it must fail against current main. Run it before your change and report
   the six names it catches.

The gate matters more than the cleanup — it is what stops this recurring.

Verify with:
  uv run pytest tests/ -q -n auto
  uv run ruff check .
  uv run lint-imports
Report all three counts. Do not run e2e.

Commit and push the branch so CI runs. Do not merge.
```

## Prompt C — the coder seam (start after B has merged)

```
cd ~/Dev/snodo-c

Confirm the worktree is clean and at origin/main before starting, and that the fix for
the shadowed node methods has landed — this task edits _execute_node and
_post_validate_node, and you must be editing the copy that actually runs.

File an issue first. If an open issue already describes this bug, stop and report
rather than implement.

Issue: post-execute validators review the previous commit when HEAD did not move

llm_validator.py:316 and acceptance.py:94 both judge `git diff HEAD~1..HEAD`. Nothing
establishes that HEAD moved during execute, so every adapter is obliged to arrange it and
nothing enforces that obligation. When an adapter does not, HEAD~1..HEAD resolves to the
previous unrelated commit, and the judges review it and pass. This has happened once.

InPlaceCoderAdapter._commit_changes (snodo/coders/base.py:158) is the only thing holding
the contract up for in-place adapters, and it returns silently on three paths: repo cannot
be opened, git add fails, nothing staged. Its docstring already concedes the post-execute
diff "would then be empty" — it is worse than empty, it is wrong and passes.
_apply_file_operations already raises ExecutionError when the coder produced no file
operations, so the uncaught case is precisely: file operations produced, commit did not
happen.

Do:

1. Add a HEAD-sha read to GitMCP (packages/snodo-tools/src/snodo/tools/git.py) — it has
   diff_between_refs, log and show but no way to read the current sha.
2. Capture the HEAD sha in the execute node before the coder runs, and carry it on
   LoopState. LoopState is serialised to a dict for langgraph, so update
   nodes/state.py's _dict_to_state / _state_to_dict too.
3. Add `base_ref` to ValidatorContext (snodo/validators/context.py) and populate it in
   validators/runner.py:232, where the context is built once per pass.
4. Have llm_validator and acceptance diff `base_ref..HEAD` instead of HEAD~1..HEAD.
   Fall back to HEAD~1 only when base_ref is absent, and when you do, say so in the
   prompt text the judge sees — a judge reviewing a fallback range should know it.
5. Make the unmoved case a blocker, not a pass: if base_ref equals the current HEAD sha
   after execute while artifacts were produced, halt with a distinct halt_type and a
   distinct audit op. Do not reuse the generic blocked path — the whole point is that
   this failure is nameable in the audit trail.

skip_engine_commit and skip_workspace_write stay as capability declarations. What changes
is that opting out no longer transfers an unenforced obligation.

Canary — required, and it must fail before your fix:
An adapter that returns file operations while its commit does not happen. Assert the run
halts with your new halt_type. Against current main this must show the run passing on the
previous commit's diff.

Verify with:
  uv run pytest tests/ -q -n auto
  uv run ruff check .
  uv run lint-imports
Report all three counts. Do not run e2e.

Commit and push the branch so CI runs. Do not merge.
```

## Prompt E — the release gate

```
cd ~/Dev/snodo-d

Confirm the worktree is clean and at origin/main before starting.

File an issue first. If an open issue already describes this bug, stop and report
rather than implement.

Issue: the release workflow publishes without a test gate or a tag/version check

.github/workflows/release.yml triggers on v*.*.*, runs `uv build --all-packages` and
`uv publish dist/*`. It does not run the suite and does not check that the pushed tag
matches the version it is about to publish. The repository already shows what that
permits: tag v0.6.1 points at commit d5b2138 "release: v0.6.0", CHANGELOG.md has no
[0.6.1] section, pyproject.toml says version = "0.6.1", and 155 commits have landed since.

Do:

1. Gate publish on the same three checks every task is gated on (pytest, ruff,
   lint-imports). A red suite must stop the publish.
2. Refuse the run when the tag does not match the version in pyproject.toml, or when
   CHANGELOG.md has no section for that version. Fail with a message that names the
   mismatch.
3. Do not retag or rewrite v0.6.1 — note the discrepancy in the CHANGELOG instead.

Cutting an actual release is out of scope for this task. Ask before doing it.

Canary — required: prove the refusal path fires. A workflow-level check needs a test that
can run without pushing a tag — extract the tag/version/CHANGELOG comparison into a script
under scripts/ that the workflow calls, and unit-test that script's refusal on a mismatched
version and a missing CHANGELOG section. A check nobody has seen fail is not a gate.

Verify with:
  uv run pytest tests/ -q -n auto
  uv run ruff check .
  uv run lint-imports
Report all three counts. Do not run e2e.

Commit and push the branch so CI runs. Do not merge.
```

---

## D — decided: closed, no task

**Resolved 2026-08-27 (Ylli).** snodo's guarantee is *if a gate is declared, it runs* — not
*a gate must exist*. A protocol authored with no post-execute validators has decided it does
not need one, and that decision is the author's. No WF3 guard for `post_execute`.

The compiler already implements the half that is snodo's job. `check_wf3`
(`compiler/verifier.py:157`) rejects any mode referencing an undefined validator, and
verification runs at protocol load (`protocols/__init__.py:124`, raising
`ProtocolWellFormednessError`; shipped templates are verified at import,
`protocols/__init__.py:54`). So a declared gate cannot silently be absent. Finding D as
originally written asked for the iff, which is the wrong property.

Worth recording as an ADR, because the asymmetry between the phases will look like a bug
again to the next reader: `check_wf3` already requires *dispatching* modes to have a
`pre_execute` validator (`verifier.py:186`). Existence is enforced **per capability, not per
phase** — a mode that can dispatch work must be gated before it does. The open question is
therefore not "must post_execute exist" but "is there a capability whose safety depends on a
post-execute gate the way `dispatch` depends on a pre-execute one". If the answer is no, this
is closed permanently rather than being rediscovered.

Two residuals, both judged not worth a worktree:

- The runtime WF3 guard in `_validate_node` (loop.py:602-609) duplicates a check the compiler
  already made. Keep it as defence-in-depth for a protocol that reached the engine without
  passing through load-time verification.
- The `not current_mode` half of the post-validate branch (loop.py:847) is unreachable only
  because `_validate_node` halts on an invalid mode first — unreachable by accident, not by
  construction.

**Consequence for the priority order:** the guarantee "a declared gate ran" is broken today by
**finding C**, not by D. A declared post-execute validator does run, and can run against the
previous commit's diff and pass. That is the enforcement claim failing at exactly the point the
protocol exists to protect, which raises C above the remaining P2 work.
