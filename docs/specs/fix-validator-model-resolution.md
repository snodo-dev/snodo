# Fix: validator model resolution when using OpenCodeAdapter

## Intent
When the coder is OpenCodeAdapter, validators must use the configured
default model (from ~/.snodo/config.yml) to evaluate specs — not the
coder's model string. Today validators are incorrectly calling LiteLLM
with the opencode model string, causing authentication failures and
wrong provider routing.

Three separate concerns are conflated and all need fixing:
1. The validator LLM call uses the wrong model
2. The validator LLM call is missing the API key
3. The opencode config section doesn't exist in the default schema

## Acceptance criteria
- Running snodo run --model opencode/google/gemini-3.5-flash uses
  the configured default model (e.g. gemini/gemini-3.5-flash) for
  all validator LLM calls, not the opencode model string
- Validator LLM calls authenticate successfully using the API key
  from snodo's providers config — no GOOGLE_API_KEY env var required
- config.yml supports an opencode section with:
    session_token_warning (default: 150000)
    session_reset_on_model_change (default: false)
- All existing tests pass
- A new test confirms validators use config model not coder model
  when coder has no _completion_fn

## Constraints
- Read validators/llm_validator.py, engine/loop.py (validator
  completion_fn fallback block), infrastructure/model_discovery.py
  (_resolve_api_key — reuse this, do not reinvent), cli/config.py
  (default config schema) before touching anything
- Do not change the LiteLLMAdapter path — only the OpenCodeAdapter
  fallback path is affected
- The fix for concern 1 and concern 2 may be in different files —
  find the right place for each

## Testing
- Unit: validator uses config model not coder model when
  completion_fn has no _completion_fn (OpenCodeAdapter case)
- Unit: opencode config defaults present in ConfigManager
- Full suite passes
