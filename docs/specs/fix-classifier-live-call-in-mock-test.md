# FIX: wave classifier makes a live call in offline mock tests

## Goal
The wave-classifier change routes the classifier to the coder model when
llm.classifier.model is unset. In offline mock tests (no ANTHROPIC_API_KEY),
that triggers a live auth failure, and test_cli_integration_with_mock asserts a
wave is assigned, which an offline run can't produce. Make the classifier
deterministic and offline in mock tests so the suite is green without keys.

## Scope
The affected engine integration test(s) and their fixtures. Do NOT change the
classifier's runtime behavior — its fail-safe (retry, log, unwaved, non-blocking)
is correct.

## Contracts
- In the mock integration test path, the wave classifier must make NO live
  provider call — mock its completion (or pin its model to the test's existing
  mock model), consistent with how the coder/validators are already mocked.
- Do NOT weaken or delete the wave assertion. Make classification deterministic
  via the mock so the assertion passes legitimately.

## Acceptance
- `pytest tests/engine/test_integration.py` passes with ANTHROPIC_API_KEY unset.
- No live LLM call originates from the classifier in any mock test.
- Full suite green offline (1797 selected).
