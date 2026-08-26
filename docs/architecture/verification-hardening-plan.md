# Hardening self-verification

Date: 2026-08-25
Follows: `maturity-assessment-2026-08.md` — "Self-verification — fragile. This is the
systemic weakness."

---

## The principle

Every failure in this class has the same shape:

> **Absence is indistinguishable from success.**

A gate that was not run looks exactly like a gate that passed. A declaration is
mistaken for the thing declared. A suite that passes is mistaken for a feature that is
covered. Nothing lies; nothing is checked either.

The work below is not six unrelated fixes. It is one instruction applied six times:
**make absence loud.**

---

## Two facts established by inspecting CI

**CI is a post-mortem, not a gate.** `.github/workflows/ci.yml` triggers on
`push: branches: [main]` and `pull_request: branches: [main]`. Agents commit to
`agent-a` / `agent-b`; those branches are merged locally and pushed. So CI runs
*after* the merge is already on main, and since no PRs are opened, the
`pull_request` trigger never fires at all. This is precisely why lint reached main
broken twice in one day and a human found it by hand both times.

**The coverage gate cannot see uncovered new code.** The test job enforces
`--cov-fail-under=63`, a global percentage. A new module landing at 0% coverage moves
that number by a fraction of a point and the gate passes. That is the QR failure
exactly: a feature merged with no test, `make check` green.

(One thing that is *not* a gap: `-m ""` in CI clears the `addopts = "-m 'not e2e'"`
default, so the e2e suite does run in CI. Only locally is it skipped by default.)

---

## The six items, in priority order

### 1 · Run the gates before the merge, not after — P0

CI must run on every branch an agent pushes, and its result must be the thing that
authorises a merge. Today the authorisation is an agent's self-report, and that report
was wrong twice on one day.

Two shapes to choose between, and this is a real decision:

- **PR-based.** Agents open a PR; CI runs on it; merge is blocked until green. Uses
  GitHub's own machinery, but changes the working style from local merges.
- **Branch-triggered.** CI runs on `push: branches: ['**']`; the merge step checks the
  branch's latest CI conclusion via `gh run list` before merging. Keeps local merges;
  needs a small merge helper.

Either way, the property is: **a merge cannot happen on an unverified branch, and an
unverified branch must be visibly different from a green one.**

### 2 · Stop trusting self-reported gate results — P0

Agents report "`uv run ruff check .` — All checks passed" and it is taken at face
value. Twice today that report was made for a command that had not been run.

The check is cheap: the merge step asks CI, not the agent. Item 1 delivers this if the
merge is made conditional on the CI conclusion rather than on the summary text.

The stronger, snodo-native version is item 5.

### 3 · Every gate needs a canary — P1

A gate that has never been observed failing is not known to gate. Three of the five
failures this week were gates that had been green for months while not enforcing
anything.

For each gate, add a test that injects the violation it exists to catch and asserts
the gate fails. `tests/golden/test_toolchain_pin.py` already sets the pattern — the
agent proved it by temporarily loosening `pytest==9.0.3` to `>=` and watching two
tests fail. Generalise it:

| Gate | Canary |
|---|---|
| import-linter | a deliberate upward import in a fixture package must break a contract |
| ruff | a fixture file with a known violation must fail lint |
| e2e | a fixture asserting the suite detects a mutated repository state |
| toolchain pin | exists already |
| acceptance validator | item 4 |

Make it a standing rule: **a new gate ships with the proof that it can fail.**

### 4 · Prove the `acceptance` validator rejects something — P1

It is the designated answer to the largest hole (nothing checks the work against the
task's acceptance criteria) and it has **not been observed rejecting anything**. The
standing empirical finding is that read-only judges pass everything, so this is not
pedantry — it is the one thing that would distinguish the new validator from a fourth
rubber stamp.

Needs one deterministic case: a task whose artifacts demonstrably fail a verifiable
criterion, asserting `acceptance` returns `warn` and names the unmet criterion. Plus
the mirror case: an uncheckable criterion returns pass, so the safe direction is
proven too.

Note also that adding it to the shipped templates does **not** add it to any existing
project — `.snodo/protocol.yml` files generated before ADR 028 will silently keep
running the old validator set. That silence is the same failure pattern; a project
running an out-of-date validator set should be able to find out.

### 5 · Put gate outcomes in the audit trail — P1

snodo's claim is that it attests to process. It currently records which validators
ran and what they said, but nothing records that the project's own gates ran, at which
commit, with what result. An auditor reading `.snodo/audit.jsonl` cannot tell whether
the merged work was ever verified.

This is the snodo-native form of item 2, and it is a product feature rather than
repo hygiene: **the audit trail should be able to answer "was this verified, and by
what?" without leaving the audit trail.**

### 6 · Measure coverage of the change, not of the repository — P2

Replace or supplement `--cov-fail-under=63` with coverage measured over the lines the
change touched. A global percentage is an artifact measurement; patch coverage is the
property measurement — did *this* work arrive covered?

This closes the loop that `acceptance` currently has to close by judgement. A command
answering it is worth more than a judge answering it, per the standing finding that
execution catches what judgement does not.

---

## What "done" looks like

Not "six issues closed". The observable outcome is:

1. No merge can occur on a branch whose gates have not run.
2. Every gate has been observed failing at least once, deliberately.
3. `acceptance` has rejected a real omission.
4. The audit trail records the verification, not just the judgement.

At that point the measurement proposed in the maturity assessment — *how often does a
`completed successfully` survive human inspection unchanged* — becomes meaningful,
because a green result would finally mean that something checked.
