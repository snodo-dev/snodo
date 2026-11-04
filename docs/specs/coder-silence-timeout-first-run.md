# Spec: Apply the coder silence timeout to the first coder

## Problem

`llm.coder.silence_timeout_seconds` reaches a coder only when the engine
respawns one after a governance-driven model change
(`engine/nodes/writeback.py`). The coder that `build_protocol_graph` builds
for a run receives `timeout_seconds` but not the silence limit, so a host-CLI
coder always halts after the class default of 600 seconds without output,
whatever the operator configured.

The subprocess adapter's `honoured_settings` also omits the setting, so
selecting a host-CLI coder with an explicit value reports it at info level as
having no effect.

## Fix

Pass `llm.coder.silence_timeout_seconds` with the other coder settings in
`build_protocol_graph`. A mode's `coder_config` still overrides it, as it does
`timeout_seconds`.

Add `silence_timeout_seconds` to `SubprocessCoderAdapter.honoured_settings`,
because the adapter reads it to bound how long the child may stay silent.
In-process and container coders still report it as inert.

Built-in coders that do not use the setting accept it, because they absorb
unknown keyword arguments. An entry-point coder's constructor already has to
accept the engine's keyword arguments: the respawn path passes this setting,
and a mode's `coder_config` keys reach the constructor unchanged. A plugin
whose constructor names only the keywords the first run passed before, without
`**kwargs`, would now fail to build.

## Tests

- the coder built for an `agy` mode with `llm.coder.silence_timeout_seconds:
  120` carries 120, not the default of 600 (fails before the fix);
- a mode `coder_config` value of 90 overrides the configured 120;
- an explicit value is not reported as inert for `opencode-cli` or `agy`, and
  is still reported for `litellm` and the container `opencode` coder.

## Verify

`uv run pytest tests/ -q -n auto -m "" && uv run ruff check . && uv run lint-imports`

## Touch

`packages/snodo-engine/src/snodo/engine/loop.py`,
`packages/snodo-engine/src/snodo/coders/subprocess_adapter.py`,
`packages/snodo-engine/src/snodo/coders/inert_settings.py`,
`tests/coders/test_subprocess_timeout.py`, `tests/coders/test_inert_settings.py`,
`docs/coders.md`, `CHANGELOG.md`, this spec.
