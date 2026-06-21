# W3-04: Property test gaps + golden file refresh

## Intent
Two focused areas: fix the severity_strings strategy gap and add the
missing high-priority property tests; refresh stale golden files and
add tool registry snapshots.

## Part 1 — Property tests

### Fix severity_strings strategy (tests/strategies.py)
Add "error" to the severity_strings strategy. It was added to
VALID_SEVERITIES but never added here. This is a one-line fix.

### Add to tests/properties/test_invariants.py

1. test_policy_error_severity_always_halts
   Property: any validator result with severity="error" → HALT under
   ALL four disagreement policies, regardless of other results.
   Use severity_strings strategy with at least one forced "error".

2. test_policy_warn_unanimous_escalates
   Property: all-warn results under unanimous policy → ESCALATE
   (not PROCEED, not HALT). This is the exact failure mode that
   caused the original validator dispatch bug — worth an invariant.

3. test_jwt_expired_token_rejected
   Property: token with exp in the past always fails verification.
   Use the existing jwt_tokens strategy, override exp to a past
   timestamp.

4. test_jwt_single_use_consumed_token_rejected
   Property: a token that has been consumed (marked used in session)
   cannot be verified again. Read how token consumption works in
   infrastructure/tokens.py before writing this.

## Part 2 — Golden files

### Regenerate stale golden files
Run: SNODO_UPDATE_GOLDENS=1 pytest tests/golden/test_template_snapshots.py
Commit the updated solo.golden.json, team.golden.json, 2+n.golden.json.

### Add tool registry golden (tests/golden/)
New file: test_tool_registry_snapshot.py
Two snapshot tests:
1. TOOL_REGISTRY keys match a golden list — catches accidental
   tool additions or removals
2. MODE_TOOL_MAP structure matches a golden — catches mode/tool
   assignment changes

Use simple assertEqual against a hardcoded expected set, not a JSON
file — the registry is already defined in code, a set comparison is
sufficient and more readable than a JSON golden.

## Acceptance criteria
- severity_strings includes "error"
- 4 new property tests added
- All 3 golden snapshot tests pass (not deselected)
- 2 new tool registry snapshot tests added
- All existing tests pass

## Testing
This ticket IS the tests. No implementation changes.

## Constraints
- Read infrastructure/tokens.py before writing JWT property tests
- Read engine/policy.py before writing policy property tests
- Run SNODO_UPDATE_GOLDENS=1 to regenerate — do not hand-edit golden files
- Touch only tests/ directory
