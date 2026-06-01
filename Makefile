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

.PHONY: studies study clean

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
