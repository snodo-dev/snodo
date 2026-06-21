# Spec: LLM tuning via config.yml (max_tokens, tool-turn limits)

## Why

max_tokens and the tool-loop turn limits are engine/model tuning, currently hardcoded —
coder max_tokens (litellm.py:50), validator tool-loop max_tokens (llm_validator.py:198),
and both _MAX_TOOL_TURNS=6 (litellm.py:31, llm_validator.py:31). Each caused a failure
this session (large-file truncation, ADR over-exploration). They must be tunable without
code edits — and without polluting protocol.yml, which stays the governance artifact.

A config system already exists: ConfigManager reads <SNODO_HOME>/config.yml with an
`engine` section + `model`, and `snodo config get/set` is wired (cli/config.py,
cli/commands/config_cmd.py). EXTEND it with an `llm` section. Do NOT create a separate
config.json — one config file, reuse the existing loader and resolve_home().

## Design

### config.yml — new optional `llm` section
llm:
coder:
max_tokens: 16000
max_tool_turns: 6
validator:
max_tokens: 1500
max_tool_turns: 6
Absent file or section -> code defaults (full backward compat).
Precedence: explicit constructor arg > config.yml value > code default.

### Typed loader (NEW snodo/infrastructure/config.py)
- pydantic LlmConfig with CoderConfig / ValidatorConfig submodels, defaults matching
  current code (coder max_tokens 16000, turns 6; validator max_tokens 1500, turns 6).
- load_llm_config() -> LlmConfig: reads resolve_home()/config.yml `llm` key, validates
  into LlmConfig (missing keys default). Lives in infrastructure so the ENGINE imports it
  — do NOT have the engine import from cli/. Reuse resolve_home(); read the existing
  config.yml (share ConfigManager's raw read if it exposes one, else read directly).

### Injection
- Coder: build_protocol_graph (loop.py:1211-1214) calls load_llm_config() once and passes
  coder.max_tokens + coder.max_tool_turns into LiteLLMAdapter. The _MAX_TOOL_TURNS module
  constant becomes an instance value (param; default = current constant).
- Validator: thread validator.max_tokens + validator.max_tool_turns via ValidatorContext
  (add fields), populated by the engine where it already sets workspace_mcp/git_mcp/phase.
  _evaluate_with_tools and _call_llm read from context, falling back to module defaults
  when absent. Do NOT read a global ConfigManager inside the validator.

### CLI
- Extend `snodo config get/set` to accept llm.* keys (e.g. llm.coder.max_tokens,
  llm.validator.max_tool_turns) in config_cmd.py's allowed sections.

## Constraints
- config.yml, not config.json. One config file. Reuse ConfigManager / resolve_home.
- Optional + backward compatible: absent -> current defaults (16000 / 1500 / 6 / 6).
- Precedence: explicit arg > config > default.
- Engine imports the loader from infrastructure, never from cli (no engine->cli dep).
- Validator config flows via ValidatorContext, not a global read.
- v1 scope is exactly these four knobs. Do NOT move temperature, single_max_tokens, or
  protocol_adherence knobs — their defaults are correct.
- protocol.yml untouched. This is infra tuning, not governance.

## Acceptance
- No `llm` section -> behaviour unchanged (defaults).
- Setting llm.coder.max_tokens changes the coder's limit with no code edit.
- Setting llm.validator.max_tool_turns changes the validator loop bound (e.g. raise for
  ADR-heavy validators) with no code edit.
- `snodo config set llm.coder.max_tokens 24000` and `get` work.
- An explicit constructor arg still overrides config.

## Tests
- load_llm_config: missing file -> defaults; partial section -> missing keys default;
  full section honored; malformed -> clear error (or default + warning).
- LiteLLMAdapter picks up max_tokens/turns from config via build_protocol_graph.
- Validator picks up max_tokens/turns via ValidatorContext.
- Precedence: explicit arg > config > default.
- CLI get/set for llm.* keys.
- Existing config / engine suites pass.
