# Spec: Affected-test fallback for infrastructure changes

## Problem

The affected-test selector recognizes the root package manifest as shared,
but ignores workspace package manifests. Python files under `scripts/` also
fall outside the mapping from Snodo modules to their importing tests.
Both kinds of change can select no tests, causing the per-task gate to skip
pytest even when existing tests exercise the changed behavior.

## Fix

Select `tests/` when the diff contains a `pyproject.toml` under `packages/`
or a Python file under `scripts/`. Treat additions, modifications, and
deletions alike; selection depends on the changed path, not whether the file
still exists.

Use the same conservative fallback as other shared infrastructure. A
script's matching test filename is not a complete dependency map: scripts
can be exercised through subprocesses, dynamic imports, or other helpers.
Running more tests for these changes is preferable to silently missing them.

The test runner and its markers are unchanged. Selecting `tests/` does not
make the fast task gate equivalent to authoritative CI or enable e2e tests.
Ordinary source-module selection, runtime-input mappings, and plain
documentation behavior remain unchanged.

## Verify

Use temporary Git repositories and committed before/after revisions to test
added, modified, and deleted package manifests and Python scripts. All must
select `tests/`, including scripts without a matching test filename.
These cases fail with an empty selection before the fix.

Retain coverage for direct-module imports, mapped runtime inputs, templates,
unmarked documentation, and unavailable base revisions. Also verify that a
deleted test is not selected and that unrelated documentation under
`scripts/` does not trigger the fallback.

## Touch

- `scripts/select_affected_tests.py`
- `tests/scripts/test_select_affected_tests.py`
- `CONTRIBUTING.md`
- `CHANGELOG.md`
- `docs/specs/affected-tests-infrastructure-fallback.md`
