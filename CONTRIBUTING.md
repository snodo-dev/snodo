# Contributing to snodo

Thanks for your interest in contributing.

## Coverage badge

After meaningful coverage changes, regenerate and commit the badge:
```bash
uv run pytest tests/ -m "" --cov=snodo --cov-report=xml
uv run genbadge coverage -i coverage.xml -o .github/badges/coverage.svg
```

## Before you start

A **Contributor License Agreement (CLA)** is required before any contribution
can be merged. The CLA bot will prompt you automatically when you open your
first pull request.

## Development setup

```bash
git clone https://github.com/snodo-dev/snodo.git
cd snodo
pip install -e ".[dev]"
```

Run the full test suite (including E2E) before submitting:

```bash
pytest
```

## Pull requests

- One concern per PR.
- Tests required for new behaviour.
- All existing tests must pass.
- Keep commits focused — squash noise before opening the PR.

## Issues

Use GitHub Issues for bugs and feature requests. For security issues see
[SECURITY.md](SECURITY.md) — do not open a public issue.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
