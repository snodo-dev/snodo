#!/usr/bin/env bash
# Files the self-verification hardening backlog as GitHub issues.
# Plan and reasoning: docs/architecture/verification-hardening-plan.md
#
# Run from the repository root:  bash scripts/file-verification-issues.sh
set -euo pipefail

# Every label this script uses, created idempotently. `gh issue create` fails the
# whole run if a label is missing, so this must come first.
gh label create verification --color 5319E7 --description "Self-verification: gates that must actually gate" 2>/dev/null || true
gh label create ci           --color 1D76DB --description "Continuous integration and the merge gate"        2>/dev/null || true
gh label create P0           --color B60205 --description "Blocking"                                          2>/dev/null || true
gh label create P1           --color D93F0B --description "High"                                              2>/dev/null || true
gh label create P2           --color FBCA04 --description "Medium"                                            2>/dev/null || true

# ─────────────────────────────────────────────────────────────── 1 · P0
gh issue create --label "P0,verification,ci" \
  --title "CI runs after the merge, so nothing gates a branch before it lands" \
  --body-file - <<'EOF'
.github/workflows/ci.yml triggers on `push: branches: [main]` and
`pull_request: branches: [main]`. Agents commit to agent-a / agent-b, those branches
are merged locally, and the merge is pushed to main. So CI runs *after* the merge is
already on main, and because no PRs are opened the pull_request trigger never fires.

CI is therefore a post-mortem, not a gate. Lint reached main broken twice in one day
and a human found it by hand both times; CI would have caught neither before the fact.

The property wanted: **a merge cannot happen on an unverified branch, and an
unverified branch must be visibly different from a green one.**

Two shapes, and this is a real decision rather than an implementation detail:

- PR-based — agents open a PR, CI runs on it, merge is blocked until green. Uses
  GitHub's own machinery; changes the working style away from local merges.
- Branch-triggered — CI runs on `push: branches: ['**']` and the merge step checks
  the branch's latest CI conclusion (e.g. `gh run list --branch`) before merging.
  Keeps local merges; needs a small merge helper.

Say which was chosen and why. Whichever it is, "CI has not run" must be a distinct,
visible state — not indistinguishable from "CI passed".
EOF

# ─────────────────────────────────────────────────────────────── 2 · P0
gh issue create --label "P0,verification" \
  --title "Merges are authorised by an agent's self-reported gate results" \
  --body-file - <<'EOF'
Agents end their work by reporting gate results — "uv run ruff check . — All checks
passed", with counts. Those reports are taken at face value and are what authorises a
merge.

Twice in one day the report was made for a command that had not been run, and main
was left failing its own lint gate. The failure mode is not dishonesty; it is that a
gate which was skipped produces the same summary as a gate which passed.

Fix the authorisation, not the reporting: the merge step should ask CI for the
branch's conclusion rather than read the agent's summary. Depends on the CI trigger
issue.

A self-reported result is evidence of intent, not of verification, and should be
labelled as such wherever it is surfaced.
EOF

# ─────────────────────────────────────────────────────────────── 3 · P1
gh issue create --label "P1,verification" \
  --title "Every gate needs a canary proving it can fail" \
  --body-file - <<'EOF'
A gate that has never been observed failing is not known to gate. Three of the five
verification failures found this week were gates that had been reporting green for
months while enforcing nothing.

For each gate, add a test that injects the violation the gate exists to catch and
asserts the gate fails.

tests/golden/test_toolchain_pin.py already sets the pattern — it was proven by
temporarily loosening `pytest==9.0.3` to `>=` and watching two tests fail. Generalise:

- import-linter: a deliberate upward import in a fixture package must break a contract
- ruff: a fixture file with a known violation must fail lint
- e2e: a fixture asserting the suite detects a mutated repository state
- toolchain pin: exists already
- acceptance validator: separate issue

Adopt as a standing rule and record it: **a new gate ships with the proof that it can
fail.** Say in the summary how each canary was verified to fail before the gate was
trusted.
EOF

# ─────────────────────────────────────────────────────────────── 4 · P1
gh issue create --label "P1,verification" \
  --title "The acceptance validator has never been observed rejecting anything" \
  --body-file - <<'EOF'
ADR 028 added a post-execute `acceptance` validator to close the largest hole in the
pipeline: nothing checked the produced work against the task's acceptance criteria. A
task merged with two of three criteria unmet — no test for the new feature, no ADR
recording a decision the code contradicted — and every validator passed.

The new validator is the designated answer. It has not been observed rejecting
anything. Its existing tests assert that the prompt produces a verdict; they do not
establish that a real judge notices a real omission. Given the standing finding that
read-only judges pass everything, that distinction is the whole question.

Wanted, deterministically:

- a case whose artifacts demonstrably fail a verifiable criterion, asserting the
  validator returns warn and names the unmet criterion;
- the mirror case: an uncheckable criterion (device behaviour, human judgement)
  returns pass, so the safe direction is proven rather than assumed.

Related and worth handling here: adding the validator to the shipped templates does
NOT add it to any existing project. A .snodo/protocol.yml generated before ADR 028
silently keeps the old validator set, and nothing tells the operator their project is
running an out-of-date one. That silence is the same failure pattern this issue is
about.
EOF

# ─────────────────────────────────────────────────────────────── 5 · P1
gh issue create --label "P1,verification" \
  --title "The audit trail records judgements but not whether the gates ran" \
  --body-file - <<'EOF'
snodo's claim is that it attests to process. The audit trail records which validators
ran and what they concluded, but nothing records that the project's own verification
commands ran, at which commit, with what result.

An auditor reading .snodo/audit.jsonl cannot answer "was this work verified, and by
what?" — which is close to the central question the artefact exists to answer.

This is the snodo-native form of the self-reporting problem: rather than trusting a
summary, the system records the verification as a first-class event with the commit it
applies to and the outcome it produced.

Worth deciding explicitly: whether an unverified merge should be representable in the
trail at all, or should be impossible.
EOF

# ─────────────────────────────────────────────────────────────── 6 · P2
gh issue create --label "P2,verification,ci" \
  --title "Coverage is measured over the repository, so new uncovered code passes" \
  --body-file - <<'EOF'
The CI test job enforces `--cov-fail-under=63`, a global percentage. A new module
landing at 0% coverage moves that number by a fraction of a point and the gate passes.

That is exactly the failure observed on the greenfield project: a feature merged with
no test at all, and the project's verification command green throughout.

Replace or supplement the global threshold with coverage measured over the lines the
change touched. A global percentage measures the artifact; patch coverage measures the
property — did *this* work arrive covered?

This also matters beyond hygiene: it moves a question currently answered by judgement
(the acceptance validator asking "is there a test?") to a question answered by a
command. The standing finding is that execution catches what judgement does not, so
the command is worth more than the judge.
EOF

echo
echo "Filed. Review with:  gh issue list --label verification"
