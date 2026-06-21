# W5-04: MCP tools list_models and resolve_model

## Intent
Expose model discovery (W5-02) and resolution (W5-03) as MCP tools so
the architect can say "use gemini" and resolve it interactively. When a
query is ambiguous, the architect presents candidates and the user picks;
the architect re-calls with an index to resolve deterministically.

## Prerequisite
W5-02-bis landed (discovery resolves its own key from config). Confirmed —
the MCP server does not prime env, so config-based key resolution is required.

## What to build

### mcp/tools.py — two new TOOL_REGISTRY entries
list_models:
  description: "List available models across configured providers"
  inputSchema: {provider: optional string}  # filter to one provider
  requires_token: False, mcp: None, method: None

resolve_model:
  description: "Resolve a model query to a concrete model. Returns exact
    match, or ambiguous candidates to pick from by index, or not_found."
  inputSchema: {query: string (required), index: optional integer}
  requires_token: False, mcp: None, method: None

### mcp/tools.py — MODE_TOOL_MAP
Add both to the "edit" mode entry (read-only, alongside list_files).

### mcp/model_handlers.py (new) — ModelToolHandler
Same pattern as JobToolHandler.

handle_list_models(arguments) -> dict
  - providers = DEFAULT_PROVIDER_CATALOG (merged with user config providers)
  - models = discover_models(providers)
  - optional provider filter
  - return {"models": [model.model_dump() for ...]}

handle_resolve_model(arguments) -> dict
  - query required, index optional
  - models = discover_models(providers)
  - result = resolve_model(query, models)
  - If exact: {"status": "exact", "model": result.match.model_dump()}
  - If ambiguous AND index provided:
      return the candidate at that index as exact:
      {"status": "exact", "model": candidates[index].model_dump()}
      (validate index in range; out of range → error dict)
  - If ambiguous AND no index:
      {"status": "ambiguous",
       "candidates": [c.model_dump() for c in result.candidates],
       "hint": "Multiple matches. Re-call resolve_model with index=N
                to pick, or a more specific query."}
  - If not_found:
      {"status": "not_found", "query": query}

### mcp/server.py
- Instantiate self._model_handler = ModelToolHandler() at init (near
  self._job_handler)
- Add two dispatch branches in call_tool:
    if name == "list_models": return self._model_handler.handle_list_models(arguments)
    if name == "resolve_model": return self._model_handler.handle_resolve_model(arguments)

## Acceptance criteria
- list_models returns discovered models, optional provider filter works
- resolve_model exact → returns the model
- resolve_model ambiguous without index → candidates + hint
- resolve_model ambiguous with valid index → resolves to that candidate
- resolve_model ambiguous with out-of-range index → clear error
- resolve_model not_found → status not_found with query
- Both tools gated to "edit" mode
- Both requires_token: False (read-only)
- MCP server has no hidden env dependency (uses config key resolution
  from W5-02-bis)

## Testing
- Unit test: handle_list_models returns models (mock discover_models)
- Unit test: provider filter narrows results
- Unit test: resolve exact → model returned
- Unit test: resolve ambiguous no index → candidates + hint
- Unit test: resolve ambiguous with index → correct candidate
- Unit test: out-of-range index → error
- Unit test: not_found → status not_found
- Unit test: tools registered in TOOL_REGISTRY and "edit" mode
- Full suite passes clean

## Constraints
- Read mcp/tools.py, mcp/server.py (call_tool dispatch, JobToolHandler
  instantiation), mcp/job_handlers.py before touching anything
- Follow the JobToolHandler pattern exactly for ModelToolHandler
- Mock discover_models in all handler tests — no live network
- Touch mcp/tools.py, mcp/server.py, new mcp/model_handlers.py only
