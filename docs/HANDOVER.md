# snodo — handover

Date: 2026-08-27. Written to start a fresh session without re-deriving context.

## Where it stands

63 commits today, 29 issues closed (#41–#99), 38 ADRs. `main` clean and pushed.
Five agent worktrees (`~/Dev/snodo-a` … `-e`) all at main and idle.

Today's wave was self-verification. Everything found was the same shape: a check
that reported success while not doing its job. All of it is closed:

- CI runs branches concurrently and gates the merged result (#92)
- pytest can no longer collect 18% of itself and report green (#98)
- a pre-execute validator can no longer veto the attempt that would fix its own
  finding (#90)
- a spec citing a file the worktree cannot see is warned about rather than
  silently invented by the coder (#93, false positive fixed in #99)
- recovery attempts are told what earlier attempts wrote, and can delete it
  (#91, #97)
- the test suite can no longer corrupt the repo's audit log, and the warning now
  names which failure occurred (#96)
- `snodo recon` works from the CLI (#95)
- ADR 038 defines the orchestrator role and what of its work belongs in the audit
  trail — decided, not built

## The measurement

`snodo task review` now records. The audit log has real verdicts for the first
time. The number that matters: of tasks that report `completed`, how many survive
inspection unchanged, and how many needed a human rescue. Keep tagging every
merge; two weeks of it answers the only question a sceptic asks.

## Known gaps, not yet scheduled

**The coder seam.** Post-execute validators judge `git diff HEAD~1..HEAD` rather
than the artifact the adapter returned (`llm_validator.py:316`,
`acceptance.py:94`). Every adapter must therefore arrange for HEAD to have moved.
When one doesn't, the range resolves to the *previous* commit and validators
review an unrelated change and pass. This has happened once. `skip_workspace_write`
and `skip_engine_commit` remain opt-outs that transfer no obligation.
ADR 035 fixed the `hasattr` duck typing; this half is untouched.

**Core rules are unenforced.** `loop.py:17` states that INV3 has "no single site" —
it holds because the code is shaped correctly, and nothing fails if that changes.
`mode` is a bare `str`. Whether this matters is an open question and was mid-
argument when the last session ended; treat it as unresolved rather than agreed.

**Operational readiness.** No version, no release, no install story for anyone
who isn't the author.

## Working conventions

- Tasks go to an agent worktree as a prompt, prefixed with `cd ~/Dev/snodo-<x>`.
- Every task: file an issue first, and **if an open issue already describes the
  bug, stop and report rather than implement** — duplicate work happened three
  times in one day without this.
- Confirm the worktree is clean and at origin/main before starting.
- Verify with `uv run pytest tests/ -q -n auto`, `uv run ruff check .`,
  `uv run lint-imports`. Report all three counts. Do not run e2e.
- **Commit and push the branch** so CI runs while the agent finishes; do not
  merge. Merging is `bash scripts/merge-agents.sh [a b c ...]`.
- A gate is not done until a canary proves it can fail. Four written this week
  each found something real the day it was written.
