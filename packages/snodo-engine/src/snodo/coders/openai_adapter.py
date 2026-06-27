"""OpenAI coder adapter.

Inherits the base LiteLLMAdapter unchanged — already OpenAI-shaped.
"""

from snodo.coders.litellm import LiteLLMAdapter


class OpenAIAdapter(LiteLLMAdapter):
    """Coder adapter for OpenAI models (gpt-*, o1-*, o3-*).

    Inherits the base _call_llm_with_tools unchanged — the OpenAI
    message shape is the native format.
    """

    TRUNCATION_REASONS: set[str] = {"length"}
