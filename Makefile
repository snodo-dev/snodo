# Snodo Wave 8 — Empirical Studies Runner
#
# Targets:
#   make studies          Run all studies headless, regenerate outputs
#   make study NAME=...   Run a single study by name
#
# Studies directory: studies/<name>/notebook.py
# Each study is a marimo notebook.
#
# Requires: pip install -r studies/requirements.txt

PYTHON := .venv/bin/python

# ──────────────────────────────────────────────
# Lockstep version management (uv workspace)
# ──────────────────────────────────────────────
# Read current root version at make-parse time.
# Recipe-level targets re-read at execution time.
_V := $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
PACKAGES := snodo-core snodo-tools snodo-foundation snodo-engine snodo-mcp
PART ?= patch

.PHONY: studies study clean version sync-versions bump release

version:
	@echo $(_V)

sync-versions:
	$(eval V := $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml))
	@echo "Syncing all packages to v$(V)"
	for p in $(PACKAGES); do \
		uv version "$(V)" --package "$$p" 2>/dev/null; \
	done
	# Rewrite all snodo-<name>==X.Y.Z pins across the workspace
	sed -i.bak 's/snodo-\([a-z]*\)==[0-9]*\.[0-9]*\.[0-9]*/snodo-\1==$(V)/g' \
		pyproject.toml packages/*/pyproject.toml
	rm -f pyproject.toml.bak packages/*/pyproject.toml.bak
	uv lock
	@echo "Done — all packages at v$(V)"

bump:
	uv version --bump $(PART)
	$(MAKE) sync-versions

release:
	@# Refuse to release from a dirty tree: `git add -A` below would otherwise
	@# sweep unrelated work into the "release:" commit, losing its own message
	@# and any `Fixes #N` attribution.
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "Working tree is dirty. Commit or stash before releasing:"; \
		git status --short; \
		exit 1; \
	fi
	@echo "Running test suite (incl. e2e)..."
	uv run pytest tests/ -q || { \
		echo "Tests failed. Aborting release."; \
		exit 1; \
	}
	uv run pytest tests/e2e/ -m e2e -q || { \
		echo "E2E tests failed. Aborting release."; \
		exit 1; \
	}
	uv run ruff check . || { echo "Lint failed. Aborting release."; exit 1; }
	uv run lint-imports || { echo "Import contracts broken. Aborting release."; exit 1; }
	$(MAKE) bump PART=$(PART)
	@# Read the version in the SHELL, after bump has run. A make-level eval here
	@# would be expanded when make expands this recipe — before any line of it runs —
	@# and would capture the pre-bump version, tagging the release with the
	@# version it just replaced.
	@V=$$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml) && \
	  uv run python scripts/check_release_version.py --tag "v$$V" || { \
		echo "Release gate failed for v$$V (see errors above)."; \
		echo "The version bump is uncommitted — nothing has been tagged or pushed."; \
		echo "Add the CHANGELOG section for v$$V, then commit, tag and push"; \
		echo "manually (git add -A && git commit -m \"release: v$$V\" &&"; \
		echo "git tag -a \"v$$V\" -m \"snodo v$$V\" && git push origin main --follow-tags),"; \
		echo "or discard the bump (git checkout -- pyproject.toml packages/*/pyproject.toml uv.lock)"; \
		echo "and re-run after writing the section."; \
		exit 1; \
	}
	@V=$$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml) && \
	  git add -A && \
	  git commit -m "release: v$$V" && \
	  git tag -a "v$$V" -m "snodo v$$V" && \
	  git push origin main --follow-tags

# ──────────────────────────────────────────────
# Experiment task selection
# ──────────────────────────────────────────────

.PHONY: exp-select exp1

exp-select:
	uv run python -m experiments.select_tasks

exp1:
	uv run python -m experiments.run_exp1 $(ARGS)

# ──────────────────────────────────────────────
# Studies
# ──────────────────────────────────────────────

studies:
	$(PYTHON) studies/run_all.py

study:
ifndef NAME
	$(error Usage: make study NAME=<study_name>  (e.g. make study NAME=_smoke))
endif
	$(PYTHON) studies/run_all.py $(NAME)

clean:
	find studies -name "*.svg" -path "*/outputs/*" -delete
	find studies -name "*.csv" -path "*/outputs/*" -delete
	@echo "Cleaned all study outputs"

# ──────────────────────────────────────────────
# Documentation
# ──────────────────────────────────────────────

.PHONY: docs docs-serve deploy-docs

docs:
	uv run --extra docs mkdocs build --strict

docs-serve:
	uv run --extra docs mkdocs serve

deploy-docs: docs
	npx wrangler pages deploy site --project-name=snodo-docs

# ──────────────────────────────────────────────
# Remote verification gate
# ──────────────────────────────────────────────
# The suite is IO-bound and parallelises well, but the dev Macs have four
# cores.  `make gate` pushes the current HEAD to a big Linux box and runs the
# checks there: same suite, ~5x faster, and on the same platform CI uses.
#
# Each working copy gets its own directory on the gate host, named after the
# directory it is run from, so agent worktrees can gate concurrently without
# clobbering each other's checkout or venv.
#
#   make gate        the fast loop: non-e2e suite, ruff, import contracts
#   make gate-ci     what CI decides on: full suite with coverage
#   make gate-init   one-time (implied by the above): create the remote repo
#
# Override GATE_HOST / GATE_HOME to point somewhere else.

GATE_HOST ?= yprift01@192.168.0.104
GATE_HOME ?= /home/yprift01
GATE_ROOT ?= $(GATE_HOME)/Dev/gates
GATE_NAME ?= $(notdir $(CURDIR))
GATE_DIR   = $(GATE_ROOT)/$(GATE_NAME)
GATE_URL   = ssh://$(GATE_HOST)$(GATE_DIR)
GATE_JOBS ?= 24
GATE_PATH  = export PATH=$(GATE_HOME)/.local/bin:$$PATH

.PHONY: gate gate-ci gate-init _gate-push

gate-init:
	@ssh $(GATE_HOST) 'mkdir -p $(GATE_DIR) && cd $(GATE_DIR) && { [ -d .git ] || { git init -q && git config receive.denyCurrentBranch updateInstead; }; }'
	@ssh $(GATE_HOST) '[ -x $(GATE_HOME)/.local/bin/uv ] || command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh'

_gate-push:
	@# Refuse rather than silently test the wrong tree: the gate runs what is
	@# committed, so uncommitted work would pass a check it never faced.
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "Working tree is dirty. The gate tests HEAD, not your changes — commit first:"; \
		git status --short; \
		exit 1; \
	fi
	@git push -q -f $(GATE_URL) HEAD:refs/heads/main

gate: gate-init _gate-push
	@ssh $(GATE_HOST) '$(GATE_PATH); cd $(GATE_DIR) && uv sync --all-extras -q && uv run pytest tests/ -q -n $(GATE_JOBS) && uv run ruff check . && uv run lint-imports'

gate-ci: gate-init _gate-push
	@ssh $(GATE_HOST) '$(GATE_PATH); cd $(GATE_DIR) && uv sync --all-extras -q && uv run pytest tests/ -m "" -n $(GATE_JOBS) --tb=short --timeout=60 --cov --cov-report=term-missing --cov-fail-under=75 && uv run ruff check . && uv run lint-imports'
