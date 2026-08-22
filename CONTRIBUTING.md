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
6. **If a change alters package structure, the execution path, or where an
   invariant is enforced, update `docs/architecture.md` in the same PR.** Keep it
   free of line numbers and of current findings — reference files and functions,
   and let GitHub Issues carry priorities.

## Testing and checks

All of these must pass before you push:

```bash
uv run pytest tests/          # unit + integration (e2e deselected by default)
uv run pytest tests/ -m ""    # everything, including e2e (~2 min)
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

## Coverage badge

After meaningful coverage changes, regenerate and commit the badge:

```bash
uv run pytest tests/ -m "" --cov=snodo --cov-report=xml
uv run genbadge coverage -i coverage.xml -o .github/badges/coverage.svg
```

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
