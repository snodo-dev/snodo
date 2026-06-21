# W6-01: Test tiering — fast default, full on demand + CI

## Intent
The full suite is 149s; 69% (103s) is 32 e2e subprocess tests (each pays
~1.5s Python-subprocess startup). The other 1649 tests run in 47s. Make
the default local run fast by excluding e2e; keep the full suite available
on demand and unchanged in CI. Evidence: e2e is the only meaningful
slowness — no xdist or restructuring needed.

## What to change

### pyproject.toml [tool.pytest.ini_options]
- addopts = "-m 'not e2e'"  (default local run skips the 32 e2e tests)
- Keep markers registered: e2e, slow, property, timeout
- The e2e marker already exists and is applied to all tests/e2e/ — no
  re-marking needed

### CI must stay full
- The CI workflow (ci.yml) currently runs pytest with no marker filter.
  It must keep running the FULL suite including e2e. addopts in pyproject
  would apply to CI too — so CI's pytest invocation must explicitly
  OVERRIDE addopts to run everything:
  pytest tests/ -m "" --tb=short --timeout=60   (or -o addopts="")
  Verify CI runs all 1681 tests after this change.

### Developer ergonomics
- Default: pytest → 1649 tests, ~47s (fast loop)
- Full: pytest -m "" or pytest -m "e2e or not e2e" → 1681, ~149s
- Document these in a brief CONTRIBUTING note or the pyproject comment:
  "default run skips e2e for speed; run `pytest -m ''` for the full suite;
  CI always runs full."

## Acceptance criteria
- pytest (default) runs ~1649 tests, excludes e2e, ~47s
- pytest -m "" runs all 1681
- CI runs the FULL suite (verify the workflow overrides addopts)
- No tests are deleted or skipped silently — e2e still runs, just not by
  default locally
- The previously-deselected golden + shell-MCP tests (now passing) are
  NOT re-deselected — the only filter is the e2e marker

## Testing
- Confirm pytest collects 1649 by default (not 1681)
- Confirm pytest -m "" collects 1681
- Confirm the CI invocation collects 1681 (override works)

## Constraints
- Read pyproject.toml [tool.pytest.ini_options] and the CI workflow
  (.github/workflows/ci.yml or equivalent) before touching anything
- The only default exclusion is e2e — do NOT exclude property, golden,
  or anything else. They're fast enough.
- CI must remain full-coverage — this is local-dev ergonomics only
- Do not delete the unused `slow` marker — leave it registered
