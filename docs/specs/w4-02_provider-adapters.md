# W4-02: Provider-specific coder adapters on top of LiteLLM

## Intent
The coder tool loop is OpenAI-shaped. LiteLLM handles completions across
providers, but the tooling round-trip (tool calls, tool results,
truncation finish_reason) doesn't normalize reliably for Anthropic and
Gemini. Extract a base adapter owning all provider-agnostic logic, with
provider subclasses overriding only the tooling shape. LiteLLM stays as
transport — the subclass handles how the conversation is shaped per provider.

## What to change

### coders/litellm.py — make LiteLLMAdapter the base class
Keep all provider-agnostic methods in the base:
- __init__, implement, _build_prompt, _call_llm (dispatch),
  _extract_submit_files, _build_tool_definitions, _execute_tool,
  _parse_response, _extract_json, attach_mcp_tool, list_available_tools

Make _call_llm_with_tools the single overridable method. The base class
keeps the current OpenAI-shaped implementation as the default.

Make _check_truncation provider-aware: accept the finish_reason and
check against a class attribute TRUNCATION_REASONS (set per subclass).
Base/OpenAI: {"length"}.

### coders/openai_adapter.py (new)
class OpenAIAdapter(LiteLLMAdapter):
  TRUNCATION_REASONS = {"length"}
  # Inherits the base _call_llm_with_tools unchanged — already OpenAI-shaped

### coders/anthropic_adapter.py (new)
class AnthropicAdapter(LiteLLMAdapter):
  TRUNCATION_REASONS = {"max_tokens"}
  Override _call_llm_with_tools:
  - Tool result feedback: Anthropic expects tool_result blocks inside a
    user message, not role:"tool" messages. Build:
    {"role": "user", "content": [{"type": "tool_result",
     "tool_use_id": tc.id, "content": str(result)}]}
  - Everything else (loop, submit_files detection, read-tool dispatch)
    inherited via super() helpers where possible. Read the base method
    first — only override what differs.

### coders/gemini_adapter.py (new)
class GeminiAdapter(LiteLLMAdapter):
  TRUNCATION_REASONS = {"MAX_TOKENS"}
  Override _call_llm_with_tools:
  - Tool result feedback: Gemini expects functionResponse parts inside
    a message, not role:"tool". This is the known-broken round-trip.
    Build the message shape LiteLLM correctly translates for Gemini —
    VERIFY the exact shape against litellm 1.83.7 before writing. Do not
    guess the Gemini message format; test it or read litellm's gemini
    transformation source.

### coders/__init__.py — routing
Add to CODER_REGISTRY:
  "openai": OpenAIAdapter,
  "anthropic": AnthropicAdapter,
  "gemini": GeminiAdapter,
Keep "litellm": LiteLLMAdapter as backward-compatible default.

Add a provider-detection helper:
  resolve_adapter_class(model: str) -> Type[Coder]
  - model starts with "gpt"/"o1"/"o3" → OpenAIAdapter
  - model starts with "claude" → AnthropicAdapter
  - model starts with "gemini" or "google/" → GeminiAdapter
  - else → LiteLLMAdapter (fallback)

### engine/loop.py — build_protocol_graph
Replace the hardcoded LiteLLMAdapter(...) construction with:
  adapter_cls = resolve_adapter_class(model)
  coder = adapter_cls(model=model, ...)

## Acceptance criteria
- LiteLLMAdapter is the base class, _call_llm_with_tools is overridable
- Three subclasses exist, each with correct TRUNCATION_REASONS
- AnthropicAdapter uses tool_result blocks for tool feedback
- GeminiAdapter uses verified Gemini message shape for tool feedback
- resolve_adapter_class routes by model prefix
- build_protocol_graph uses resolve_adapter_class
- Existing tests pass — default path (claude-sonnet) now routes to
  AnthropicAdapter instead of base LiteLLMAdapter; if that breaks tests,
  the tests were asserting on the base class — update them

## Testing
- Unit test: resolve_adapter_class for each prefix → correct class
- Unit test: AnthropicAdapter tool result message shape
- Unit test: GeminiAdapter tool result message shape
- Unit test: _check_truncation per subclass — each detects its own
  truncation reason, ignores others
- Unit test: OpenAIAdapter inherits base behavior unchanged
- Full suite passes clean

## Constraints
- Read coders/litellm.py in full before touching anything
- VERIFY Gemini message shape against litellm — do not guess
- Do not change the Coder ABC or CodeArtifact
- Subclasses override the minimum — inherit everything else
