"""Coder adapter registry.

FILE: snodo/coders/__init__.py

Registry pattern for pluggable coder backends.
"""

from typing import Any, Dict, List, Optional, Type

from snodo.core.interfaces import Coder, MCPServer
from snodo.coders.base import CoderAdapter, AdapterError as AdapterError, LLMCallError as LLMCallError, ParseError as ParseError
from snodo.coders.litellm import LiteLLMAdapter
from snodo.coders.mock import MockAdapter

# Backward-compatible aliases
BasicCoderAdapter = LiteLLMAdapter
MockCoderAdapter = MockAdapter

# Registry of available coder backends
CODER_REGISTRY: Dict[str, Type[CoderAdapter]] = {
    "litellm": LiteLLMAdapter,
    "mock": MockAdapter,
    # Future: "claude_code", "aider", "opencode"
}


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
    model: str = "gpt-4",
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
