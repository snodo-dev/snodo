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

# What a release must pass before it is tagged. `gate-ci` is the check that
# decides: it runs on the gate host against the pushed HEAD — the very commit
# about to be tagged, since a release refuses a dirty tree — with the marker
# filter cleared, so the e2e tests, coverage and patch coverage are all
# included alongside the suite, ruff, the import contracts and the three
# ratchets. Re-running any of that here would derive the same answer a second
# time on a weaker machine; the release used to, and it cost a full extra suite
# per release while still never checking coverage.
#
# Override when the gate host is unreachable: `make release PART=minor
# RELEASE_GATE=check` falls back to the local loop, which does NOT include the
# e2e tests or either coverage gate. It is a way to ship with the lights off,
# not an equal path.
RELEASE_GATE ?= gate-ci

release:
	@# Refuse to release from a dirty tree: `git add -A` below would otherwise
	@# sweep unrelated work into the "release:" commit, losing its own message
	@# and any `Fixes #N` attribution. It also keeps the gate honest: the gate
	@# tests the pushed HEAD, which is only this tree when this tree is clean.
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "Working tree is dirty. Commit or stash before releasing:"; \
		git status --short; \
		exit 1; \
	fi
	$(MAKE) $(RELEASE_GATE) || { \
		echo "$(RELEASE_GATE) failed. Aborting release."; \
		exit 1; \
	}
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
#   make gate        the fast loop: non-e2e suite, ruff, import contracts,
#                    file-length ratchet, docs-coverage ratchet, vocabulary and changelog checks
#   make gate-ci     what CI decides on: full suite with coverage
#   make gate-init   one-time (implied by the above): create the remote repo
#
# Override GATE_HOST / GATE_HOME to point somewhere else.
#
# A gate's remote work ends when the invocation that started it ends. The
# session is run over a terminal (`ssh -tt`), so an interrupt, a dropped link
# or a killed `make` hangs up the remote command, and the trap in
# scripts/gate_remote.sh reaps its whole process tree — pytest and its xdist
# workers — with the leader. The alternative, an explicit bound on the run,
# would cut a slow gate short rather than a dead one; a pty hangup is what
# actually reaches a process whose parent is gone. Nothing here changes what a
# gate checks or the order it checks it in.
#
# Concurrency is bounded, not forbidden. Several worktrees gating at once is
# the point of GATE_NAME; several taking the host down is not. GATE_SLOTS
# gates share a host at once through flock slots under GATE_ROOT, so a burst
# of abandoned-plus-live runs waits instead of oversubscribing the box until a
# login shell takes tens of seconds. Waiting is silent; a clean gate prints
# exactly what it always did.

GATE_HOST ?= yprift01@192.168.0.104
GATE_HOME ?= /home/yprift01
GATE_ROOT ?= $(GATE_HOME)/Dev/gates
GATE_NAME ?= $(notdir $(CURDIR))
GATE_DIR   = $(GATE_ROOT)/$(GATE_NAME)
GATE_URL   = ssh://$(GATE_HOST)$(GATE_DIR)
GATE_JOBS ?= 24
GATE_SLOTS ?= 2
GATE_PATH  = export PATH=$(GATE_HOME)/.local/bin:$$PATH
# Environment the remote wrapper reads: where uv lives, where the shared flock
# slots live (GATE_ROOT, shared by every worktree on the host), and the two
# bounds. The recipe below has already cd'd into this worktree's GATE_DIR.
GATE_ENV   = GATE_HOME=$(GATE_HOME) GATE_ROOT=$(GATE_ROOT) GATE_JOBS=$(GATE_JOBS) GATE_SLOTS=$(GATE_SLOTS)
# -q keeps ssh's own "Connection closed" notice out of a run; the remote
# wrapper supplies the terminal (`-tt`) the hangup needs.
GATE_SSH   = ssh -q -tt $(GATE_HOST)
# The remote gate receives this explicitly because its checkout is a mirror of
# the pushed HEAD and cannot infer the agent branch's base after the push.
GATE_BASE  ?= $(shell git merge-base origin/main HEAD 2>/dev/null)

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

# The wrapper runs from the pushed HEAD, so it is always the same revision the
# gate is testing; GATE_DIR is this worktree's checkout after _gate-push.
gate: gate-init _gate-push
	@$(GATE_SSH) '$(GATE_PATH); cd $(GATE_DIR) && $(GATE_ENV) CHANGELOG_BASE=$(GATE_BASE) bash scripts/gate_remote.sh gate'

gate-ci: gate-init _gate-push
	@$(GATE_SSH) '$(GATE_PATH); cd $(GATE_DIR) && $(GATE_ENV) CHANGELOG_BASE=$(GATE_BASE) bash scripts/gate_remote.sh gate-ci'

# ──────────────────────────────────────────────
# Agent worktrees
# ──────────────────────────────────────────────
# The agent worktrees are siblings of this checkout: ../snodo-a … ../snodo-e,
# each on its own agent-<x> branch.  Make has no "--flags" of its own to lend a
# target, so the selection travels in W — a comma or space separated list of
# worktree letters, defaulting to all of them.
#
#   make wt-check                what is unmerged, stale or uncommitted
#   make wt-rebase W=a,d         rebase those worktrees onto origin/main
#   make wt-merge  W=a,c         rebase, gate, merge, push — one at a time
#
# wt-merge stops at the first failure rather than carrying on: a worktree whose
# gate fails must not be followed by another merge on top of it.

WT_ROOT   ?= $(abspath $(CURDIR)/..)
WT_PREFIX ?= snodo-
WT_BRANCH ?= agent-
W         ?= a b c d e
_comma    := ,
_empty    :=
_space    := $(_empty) $(_empty)
_W         = $(subst $(_comma),$(_space),$(W))

.PHONY: wt-check wt-rebase wt-merge

wt-check:
	@git fetch -q origin
	@for w in $(_W); do \
		b=$(WT_BRANCH)$$w; d=$(WT_ROOT)/$(WT_PREFIX)$$w; \
		if [ ! -d "$$d" ]; then printf '%-10s (missing)\n' "$(WT_PREFIX)$$w"; continue; fi; \
		printf '%-10s ahead:%-3s behind:%-3s dirty:%-3s %s\n' \
			"$(WT_PREFIX)$$w" \
			"$$(git rev-list --count main..$$b 2>/dev/null || echo ?)" \
			"$$(git rev-list --count $$b..main 2>/dev/null || echo ?)" \
			"$$(git -C $$d status --porcelain 2>/dev/null | wc -l | tr -d ' ')" \
			"$$(git --no-pager log --format=%s -1 $$b 2>/dev/null)"; \
	done

wt-rebase:
	@git fetch -q origin
	@for w in $(_W); do \
		d=$(WT_ROOT)/$(WT_PREFIX)$$w; \
		echo "── $(WT_PREFIX)$$w"; \
		if [ ! -d "$$d" ]; then echo "   missing, skipped"; continue; fi; \
		if [ -n "$$(git -C $$d status --porcelain)" ]; then \
			echo "   DIRTY — refusing to rebase over uncommitted work:"; \
			git -C $$d status --short; exit 1; \
		fi; \
		git -C $$d rebase origin/main || { echo "   rebase failed"; exit 1; }; \
	done

wt-merge:
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "This checkout is dirty — commit or stash before merging."; exit 1; fi
	@git fetch -q origin
	@for w in $(_W); do \
		b=$(WT_BRANCH)$$w; d=$(WT_ROOT)/$(WT_PREFIX)$$w; \
		echo "── $(WT_PREFIX)$$w"; \
		if [ ! -d "$$d" ]; then echo "   missing, skipped"; continue; fi; \
		if [ -n "$$(git -C $$d status --porcelain)" ]; then \
			echo "   DIRTY — commit it first:"; git -C $$d status --short; exit 1; fi; \
		if [ "$$(git rev-list --count main..$$b)" = "0" ]; then \
			echo "   nothing to merge"; continue; fi; \
		git -C $$d rebase origin/main || { echo "   rebase failed"; exit 1; }; \
		$(MAKE) -C $$d gate || { echo "   gate failed — stopping before merge"; exit 1; }; \
		git checkout -q main && git pull -q --ff-only || exit 1; \
		git merge --no-ff $$b -m "Merge $$b: $$(git --no-pager log --format=%s -1 $$b)" || exit 1; \
		git push -q origin main || exit 1; \
		echo "   merged and pushed"; \
	done
