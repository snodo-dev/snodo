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
	@echo "Running test suite..."
	uv run pytest tests/ -q || { \
		echo "Tests failed. Aborting release."; \
		exit 1; \
	}
	$(MAKE) bump PART=$(PART)
	$(eval V := $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml))
	git add -A
	git commit -m "release: v$(V)"
	git tag -a "v$(V)" -m "snodo v$(V)"
	git push origin main --follow-tags

# ──────────────────────────────────────────────
# Experiment task selection
# ──────────────────────────────────────────────

.PHONY: exp-select

exp-select:
	uv run python -m experiments.select_tasks

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
