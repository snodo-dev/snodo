# FIX: green test_cli_integration_with_mock (test-side only)

## Goal
test_cli_integration_with_mock was the only originally-failing test. Two issues:
the wave classifier makes a live provider call (no key in test env), and the
file-existence assertion checks temp_git_repo while execution writes into the
worktree. Make the test pass offline. This is a TEST change only.

## Scope
tests/engine/test_integration.py and its fixtures only.

## Contracts
1. The wave classifier must make NO live provider call in this test — mock its
   completion or pin its model to the test's existing mock, consistent with how
   the coder is already mocked. Classification should be deterministic.
2. The file-existence assertion must check where execution actually writes (the
   worktree under .snodo-worktrees/<task_id>) or the engine's reported artifacts
   — not temp_git_repo root. Do not weaken it to a trivial truthy check; verify
   the artifact genuinely exists where execution puts it.

## HARD do-not-touch
- Do NOT change classifier runtime behavior, validator logic, completion_fn
  resolution, or engine/worktree execution. (The validator regression in the
  last attempt came from touching completion_fn — stay out of it.)
- test_warn_when_no_completion_fn_but_has_criteria must stay green, untouched.

## Acceptance
- test_cli_integration_with_mock passes with ANTHROPIC_API_KEY unset.
- No live LLM call originates from the classifier in any mock test.
- The full suite is green (1797 selected) with no other test modified.
