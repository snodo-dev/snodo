"""Gemini coder adapter.

Inherits the base LiteLLMAdapter. LiteLLM 1.83.7 internally transforms
OpenAI-format role:"tool" messages to Gemini's functionResponse parts,
so _call_llm_with_tools is inherited unchanged.

Verified transformation (litellm_core_utils/prompt_templates/factory.py):
  {"role": "tool", "tool_call_id": "call_abc", "content": "..."}
→ {"function_response": {"name": "read_file", "response": {"content": "..."}}}

The tool_call_id must match the assistant message's tool_calls[].id,
which our base _call_llm_with_tools already does.
"""

from snodo.coders.litellm import LiteLLMAdapter


class GeminiAdapter(LiteLLMAdapter):
    """Coder adapter for Google Gemini models (gemini/*, google/gemini-*).

    LiteLLM transforms our OpenAI-format tool messages internally:
      {"role": "tool", "tool_call_id": "...", "content": "..."}
    → {"function_response": {"name": "...", "response": {"content": "..."}}}

    No override of _call_llm_with_tools needed.
    """

    TRUNCATION_REASONS: set[str] = {"MAX_TOKENS"}
