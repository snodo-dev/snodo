"""Coder adapter registry.

FILE: snodo/coders/__init__.py

Registry pattern for pluggable coder backends.
"""

from typing import Any, Dict, List, Optional, Type

from snodo.core.interfaces import Coder, MCPServer
from snodo.coders.base import CoderAdapter, AdapterError as AdapterError, LLMCallError as LLMCallError, ParseError as ParseError
from snodo.coders.litellm import LiteLLMAdapter
from snodo.coders.mock import MockAdapter
from snodo.coders.openai_adapter import OpenAIAdapter
from snodo.coders.anthropic_adapter import AnthropicAdapter
from snodo.coders.gemini_adapter import GeminiAdapter
from snodo.coders.opencode_adapter import OpenCodeAdapter
from snodo.infrastructure.config import DEFAULT_MODEL

# Backward-compatible aliases
BasicCoderAdapter = LiteLLMAdapter
MockCoderAdapter = MockAdapter

# Registry of available coder backends
CODER_REGISTRY: Dict[str, Type[CoderAdapter]] = {
    "litellm": LiteLLMAdapter,
    "mock": MockAdapter,
    "openai": OpenAIAdapter,
    "anthropic": AnthropicAdapter,
    "gemini": GeminiAdapter,
    "opencode": OpenCodeAdapter,
}


def resolve_adapter_class(model: str) -> Type[CoderAdapter]:
    """Resolve the appropriate coder adapter class for a model string.

    Args:
        model: Model identifier (e.g., "claude-sonnet-4-20250514", "gpt-4o")

    Returns:
        CoderAdapter subclass best suited for the model.
    """
    if model.startswith(("gpt", "o1", "o3")):
        return OpenAIAdapter
    if model.startswith("claude"):
        return AnthropicAdapter
    if model.startswith(("gemini", "google/")):
        return GeminiAdapter
    if model.startswith("opencode/"):
        return OpenCodeAdapter
    return LiteLLMAdapter


def get_coder(name: str, **config: Any) -> CoderAdapter:
    """Get a coder adapter by registry name.

    Args:
        name: Registered coder name (e.g., "litellm", "mock")
        **config: Configuration passed to the adapter constructor

    Returns:
        Initialized CoderAdapter instance

    Raises:
        KeyError: If name is not in the registry
    """
    if name not in CODER_REGISTRY:
        available = ", ".join(sorted(CODER_REGISTRY.keys()))
        raise KeyError(f"Unknown coder '{name}'. Available: {available}")
    return CODER_REGISTRY[name](**config)


def create_coder(
    model: str = DEFAULT_MODEL,
    mcp_servers: Optional[List[MCPServer]] = None,
    mock: bool = False
) -> Coder:
    """Factory function to create a Coder instance.

    Args:
        model: Model identifier
        mcp_servers: List of MCP servers
        mock: If True, return MockAdapter for testing

    Returns:
        Coder instance
    """
    if mock:
        return MockAdapter()

    return LiteLLMAdapter(
        model=model,
        mcp_servers=mcp_servers
    )
