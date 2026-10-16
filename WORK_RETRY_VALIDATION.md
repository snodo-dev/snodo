# Retry workspace validation

Validation is blocked in the supplied tool environment: the declared pytest
runner invokes `/Users/ylliprifti/.local/share/uv/tools/snodo/bin/python3`, which
fails with `No module named pytest`. Both the targeted retry/surface tests and
the full-suite invocation were attempted and failed before collection. No
passing suite, coverage result, patch-coverage result, or clean lint result is
claimed. File operations are staged atomically by the available editing tool;
the test tool does not expose a shell for installing dependencies or running
Ruff and the patch-coverage script.

Required follow-up in a development environment after applying the changes:

```sh
uv sync --all-extras
uv run pytest tests/ -m "" -n auto --tb=short --timeout=60 \
  --cov --cov-report=term-missing --cov-fail-under=80
uv run python scripts/enforce_patch_coverage.py
uv run ruff check .
uv run lint-imports
uv run python scripts/enforce_file_length.py
uv run python scripts/enforce_docs_coverage.py
uv run python scripts/enforce_vocabularies.py
```

Known remaining integration gap: concurrent plan child-job dispatch does not
yet forward `fresh_start` to the child CLI. Inline task and inline plan retry
selection are wired; concurrent plan retry must be wired and validated before
this change meets the complete acceptance criteria.
