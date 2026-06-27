"""Anthropic coder adapter.

Inherits the base LiteLLMAdapter. LiteLLM 1.83.7 internally transforms
OpenAI-format role:"tool" messages to Anthropic's tool_result blocks,
so _call_llm_with_tools is inherited unchanged.
"""

from snodo.coders.litellm import LiteLLMAdapter


class AnthropicAdapter(LiteLLMAdapter):
    """Coder adapter for Anthropic Claude models.

    LiteLLM transforms our OpenAI-format tool messages internally:
      {"role": "tool", "tool_call_id": "...", "content": "..."}
    → {"role": "user", "content": [{"type": "tool_result",
        "tool_use_id": "...", "content": "..."}]}

    No override of _call_llm_with_tools needed.
    """

    TRUNCATION_REASONS: set[str] = {"max_tokens"}
