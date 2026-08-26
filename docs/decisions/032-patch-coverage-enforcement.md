# ADR 032 — Patch coverage measurement over modified lines

## Status
Accepted

## Context
Previously, CI enforced `--cov-fail-under=63`, a global percentage over the entire repository. Global coverage measures the artifact as a whole, so a new module landing at 0% test coverage moves the global percentage by only a fraction of a point, passing the gate undetected.

## Decision
1. **Patch / Diff Coverage Measurement**:
   - Test coverage is measured over the lines touched by the change (`git diff` against base ref `origin/main`), rather than solely over the repository as a whole.
   - On a branch with several commits, the diff compares `<base_ref>..HEAD`, measuring line coverage across all commits on the branch.
   - On a change that only deletes code or contains no added executable Python lines, patch coverage is 100% (exempt / N/A).

2. **Global Threshold vs Patch Threshold**:
   - We **KEEP both thresholds (supplementing global coverage with patch coverage)**:
     - Global `--cov-fail-under=63` acts as a repository-wide safety floor against baseline regression.
     - Patch coverage (target: >=80.0%) guarantees that new and modified executable Python code arrives covered by tests.
