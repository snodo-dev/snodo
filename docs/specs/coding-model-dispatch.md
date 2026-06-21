# Spec: coding_model param on dispatch_task + validator config cleanup

## 1. dispatch_task MCP tool — add optional coding_model

In mcp/tools.py, add an optional `coding_model: str | None = None` 
parameter to the dispatch_task tool definition.

When provided: pass it as the model to build_protocol_graph (same 
path --model uses in run_cmd.py).
When absent: existing resolution chain unchanged 
(config.yml model → DEFAULT_MODEL).

The parameter must appear in the tool's MCP schema so the architect 
can set it at dispatch time.

## 2. Config schema cleanup — rename validator_llm → validator

In infrastructure/config.py, rename the validator_llm config key 
to validator to match the llm.coder naming.

Current: config.yml llm.validator_llm.model
Target:  config.yml llm.validator.model

Update the resolver in engine/loop.py accordingly. Maintain 
backwards-compat: if validator_llm key is present and validator 
is absent, read from validator_llm (deprecation shim, no warning 
needed yet).

## Tests

- dispatch_task with coding_model="deepseek/deepseek-chat" → 
  engine receives that model string as coder model
- dispatch_task without coding_model → coder model resolves from 
  config as before
- config with llm.validator.model → picked up correctly
- config with legacy llm.validator_llm.model → still works

## Touch only
mcp/tools.py, mcp/decision_handlers.py (if dispatch flows through 
there), run_cmd.py (if wiring needed), infrastructure/config.py, 
engine/loop.py

Commit: feat(dispatch): optional coding_model param + validator config rename
