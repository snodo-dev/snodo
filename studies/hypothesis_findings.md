# Property-Based Validation — Findings

**Run date:** 2026-08-10
**Host:** yp-us-lg2 (GPU2 dev machine), Python 3.12, env built via `uv sync` from `uv.lock`
**Command:** `SNODO_HYPOTHESIS_PAPER=1 uv run --no-sync pytest tests/properties -q --durations=0`
**Result:** **17 passed, 0 failed, 0 violations** in 1026.05 s (17 min 06 s)
**Raw log:** `studies/properties_paper_run.log` (committed alongside this file)

## Profile

`tests/strategies.py::hypothesis_settings()` selects `max_examples` by env var:
default 100 · `SNODO_HYPOTHESIS_LONG=1` → 1,000 · `SNODO_HYPOTHESIS_PAPER=1` → 10,000.

16 of 17 tests use this shared profile (10,000-example budget each in paper mode);
`test_loopstate_dict_roundtrip` carries its own `@settings` and runs at the
Hypothesis default (100). Tests over small discrete input spaces (e.g. the two
severity-cap tests: 3 severities × 2 caps) exhaust their space and terminate
early — the budget is an upper bound, not a guaranteed count. For this reason
the paper reports the per-test budget rather than a summed example count.

## Per-test wall time (paper mode, this run)

| Test | Invariant / property | Wall time |
|---|---|---|
| test_session_decision_roundtrip | INV5 checkpoint round-trip | 138.80 s |
| test_audit_chain_tamper_detected | INV4 tamper detection | 121.84 s |
| test_audit_chain_integrity_after_events | INV4 chain integrity | 109.73 s |
| test_policy_warn_unanimous_escalates | policy halt | 67.08 s |
| test_jwt_expired_token_rejected | INV1 TTL | 67.05 s |
| test_jwt_valid_token_verifies | INV1 | 66.59 s |
| test_jwt_single_use_consumed_token_rejected | INV1 single-use | 65.88 s |
| test_policy_halt_when_any_blocker | INV3 surface in policy | 65.74 s |
| test_policy_proceed_when_all_pass | policy | 65.27 s |
| test_jwt_wrong_task_rejected | INV1 binding | 64.83 s |
| test_jwt_tampered_rejected | INV1 signature | 64.49 s |
| test_policy_error_severity_always_halts | fail-closed on validator error | 63.88 s |
| test_wf1_modes_have_disjoint_tools | WF1 | 42.03 s |
| test_files_in_scope_deterministic | predicate determinism | 13.80 s |
| test_loopstate_dict_roundtrip | engine state round-trip | 8.02 s |
| test_severity_cap_never_increases_severity | cap monotonicity | 0.01 s (space exhausted) |
| test_severity_cap_preserves_pass | cap monotonicity | 0.01 s (space exhausted) |

## Notes

- The JWT and audit tests are slow by design: every example performs real HMAC
  signing/verification or hash-chained disk appends — no mocking of the
  invariant-bearing operations.
- This run post-dates the severity-cap audit patch (`severity_cap_applied`
  events; `severity_original` in audit results) and the mode-change audit hook;
  both cap-monotonicity properties pass against the patched code.
- Paper claims backed by this file: Implementation §Property-based validation
  and the theorem section's corroboration sentence (17 tests, 10,000-example
  budget per test, zero violations, ~17 min).

## Reproduce

```bash
uv sync
SNODO_HYPOTHESIS_PAPER=1 uv run --no-sync pytest tests/properties -q --durations=0
```
