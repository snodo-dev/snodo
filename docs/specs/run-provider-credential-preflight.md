# Spec: provider credential preflight before snodo run starts work

## Root cause
`snodo run` does not check for provider credentials before starting work. On a fresh
install it loads the protocol, opens a session, creates a worktree, builds the MCP graph,
compiles it, begins executing, and only then fails with
`litellm.AuthenticationError: Missing Anthropic API Key ...`.

`provider_env(model)` in `packages/snodo-core/src/snodo/config.py` sets the provider's
environment variables and yields; it never verifies one resolved.

## Fix
Add a preflight in `snodo/cli/commands/run_cmd.py`, before any session, worktree or graph
work: resolve the model's provider, confirm a credential is available for it, and if not,
fail immediately with a message naming the provider, the environment variable or config key
expected, and the command to set it. One line of output, exit 1, nothing created.

- Resolution reuses `ConfigManager._provider_for_model` and `get_key_for_model` — the same
  helpers the key-loading path uses, so the preflight cannot disagree with the thing that
  actually loads the key. `config.py` is not changed.
- A credential counts as present if the key is stored in config (`get_key_for_model`) or the
  provider's `api_key_env` is set in the environment.
- A provider that declares no credential env var (e.g. a local endpoint) is not preflighted.
- The check is skipped when the coder is mocked (`--mock`) or when no LLM will be called.

### Scope
`snodo/cli/commands/run_cmd.py` and its tests only.

## Tests
- a run with no credential fails before a session or worktree exists and names the provider
- a run with a credential (env or config) proceeds unchanged
- `--mock` skips the check entirely

## Verify
`uv run pytest tests/ -q -n auto -m "" && uv run ruff check . && uv run lint-imports`

## Touch
`snodo/cli/commands/run_cmd.py`, `tests/cli/test_run_cmd.py`, `CHANGELOG.md`, this spec.
