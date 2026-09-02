# Contributing to snodo

Thanks for your interest in contributing.

## Before you start

A **Contributor License Agreement (CLA)** is required before any contribution
can be merged. The CLA bot will prompt you automatically when you open your
first pull request.

## Development setup

```bash
git clone https://github.com/snodo-dev/snodo.git
cd snodo
uv sync --all-extras
```

`snodo` is **not on PATH** outside its own checkout when developing from
source, and `uv tool install snodo` pulls the five sub-packages from PyPI, so
they are **not editable** — which defeats the purpose when patching the engine.
Develop against the editable checkout by aliasing it:

```bash
alias snodo='uv run --project ~/path/to/snodo snodo'
```

`uv run --project` resolves the `snodo` entry point from the workspace's own
dependencies (all five sub-packages are editable, so edits apply immediately).
Verify the alias before first use: `snodo --version`.

Useful entry points:

```bash
snodo init --template solo                 # scaffold a protocol in the current repo
snodo run "implement hello world" --mock   # deterministic, no API calls
```

## Workflow

1. **Every change starts as a GitHub issue.** Label it `hardening`, `bug`,
   `security`, `tech-debt`, or `documentation`.
2. **Design rationale goes in `docs/specs/`** — one markdown file per change,
   describing intent and contract. Specs are write-once design records, not a
   tracker; status lives on the issue.
3. **Decisions go in `docs/decisions/` as an ADR** when you choose between
   viable alternatives or accept a risk. Number sequentially and add it to the
   ADR index.
4. **Commits reference the issue**: `Fixes #N` when the change completes it,
   `Refs #N` when partial. GitHub closes the issue on push.
5. **Every user-visible change adds a `CHANGELOG.md` entry** under `Unreleased`.
   `CHANGELOG.md` is marked `merge=union` in `.gitattributes`, so parallel
   agents each appending at the top of `### Added` merge as "keep both" with
   no conflict (see `tests/golden/test_changelog_union_merge.py`). Keep your
   entry self-contained under one bullet; do not rewrite or reorder other
   agents' entries within a single change.
6. **If a change alters package structure, the execution path, or where an
   invariant is enforced, update `docs/architecture.md` in the same PR.** Keep it
   free of line numbers and of current findings — reference files and functions,
   and let GitHub Issues carry priorities.

## Testing and checks

There are **two gates, and they are not the same**. Read the one you ran.

### Local (fast) gate — reduced, does not decide

```bash
uv run pytest tests/          # ~47s — unit + integration; e2e DESELECTED
```

`pyproject.toml` sets `addopts = "-m 'not e2e'"`, so this command **skips the
e2e suite** (about 124 tests) for a fast loop. It is the quick check, **not** the
gate that decides. It announces the reduction on the same screen and prints the
full command: a green run here does **not** mean CI is green, because the reduced
command cannot see the e2e class of failure at all.

### The gate CI runs — this is what decides

CI clears the marker filter and adds two checks the local gate never runs:

```bash
uv run pytest tests/ -m "" -n auto --tb=short --timeout=60 \
  --cov --cov-report=term-missing --cov-fail-under=75
uv run python scripts/enforce_patch_coverage.py
```

That is the whole suite (e2e included) plus a **75% total-coverage floor** and an
**80% patch-coverage check**. Run it before pushing — do not stop at the fast
gate. This is also exactly what every task ticket's verification line refers to
when it points at the full suite.

### Always-pass checks (local and CI)

```bash
uv run ruff check .           # lint
uv run lint-imports           # architecture layering contract
```

Test suites you can target individually:

```bash
uv run pytest tests/engine -v
uv run pytest tests/ -m e2e        # subprocess CLI journeys
uv run pytest tests/ -m property   # hypothesis invariant suite
```

Notes:

- `tests/golden/` snapshots protocol templates and the tool registry; if a change
  is intentional, regenerate the goldens and say so in the PR.
- **A new verification gate ships with a canary proving it can fail.** A gate that
  has never been observed failing cannot be trusted to gate. For every gate, add
  a test that injects the violation the gate exists to catch and asserts that the gate fails (Fixes #58).
- `tests/properties/test_invariants.py` covers INV1–INV5. Changes touching tokens,
  capability boundaries, blockers, the audit chain, or session resumability should
  extend it rather than only adding unit tests.

## Pull requests

- One concern per PR.
- Tests required for new behaviour.
- All existing tests must pass, including e2e.
- Keep commits focused — squash noise before opening the PR.

## Issues

Use GitHub Issues for bugs and feature requests. For security issues see
[SECURITY.md](SECURITY.md) — do not open a public issue.

Note the project's threat model (ADR 014): snodo assumes the repository it is
initialised in is **trusted**. Reports that depend on running snodo against
hostile third-party code are out of scope.

## Coverage reporting

Coverage is part of the CI gate above, not the local fast gate. CI enforces a
**75% total repo coverage floor** (`--cov-fail-under=75`) and an **80% patch
coverage** floor (`scripts/enforce_patch_coverage.py`), and publishes live to
[Codecov](https://codecov.io/gh/snodo-dev/snodo). Reproduce both locally with the
full-gate command from [Testing and checks](#testing-and-checks):

```bash
uv run pytest tests/ -m "" -n auto --cov --cov-report=term-missing --cov-fail-under=75
uv run python scripts/enforce_patch_coverage.py
```

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
