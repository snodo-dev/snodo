"""Coder adapter registry.

FILE: snodo/coders/__init__.py

Registry pattern for pluggable coder backends.
"""

from typing import Any, Dict, Optional, Type

from snodo.coders.base import (
    CoderAdapter,
    AdapterError as AdapterError,
    LLMCallError as LLMCallError,
    ParseError as ParseError,
    TurnBudgetExhausted as TurnBudgetExhausted,
)
from snodo.coders.litellm import LiteLLMAdapter
from snodo.coders.mock import MockAdapter
from snodo.coders.openai_adapter import OpenAIAdapter
from snodo.coders.anthropic_adapter import AnthropicAdapter
from snodo.coders.gemini_adapter import GeminiAdapter
from snodo.coders.opencode_adapter import OpenCodeAdapter
from snodo.coders.opencode_cli_adapter import OpenCodeCLIAdapter
from snodo.coders.agy_adapter import AGYAdapter
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
    "opencode-cli": OpenCodeCLIAdapter,
    "agy": AGYAdapter,
}


def resolve_coder_name(
    model: str = DEFAULT_MODEL,
    mode_coder: Optional[str] = None,
    cli_coder: Optional[str] = None,
    use_mock: bool = False,
) -> str:
    """Resolve the coder registry name following precedence:
    1. Explicit mock flag (use_mock / --mock)
    2. Explicit CLI choice (cli_coder / --coder)
    3. Protocol mode choice (mode_coder / mode.coder)
    4. Model string prefix mapping (opencode-cli/, opencode/, agy/, gpt/o1/o3, claude, gemini)
    5. Default fallback ('litellm')
    """
    if use_mock:
        return "mock"
    if cli_coder:
        return cli_coder
    if mode_coder:
        return mode_coder
    if model:
        if model.startswith("opencode-cli/"):
            return "opencode-cli"
        if model.startswith("opencode/"):
            return "opencode"
        if model.startswith("agy/"):
            return "agy"
        if model.startswith(("gpt", "o1", "o3")):
            return "openai"
        if model.startswith("claude"):
            return "anthropic"
        if model.startswith(("gemini", "google/")):
            return "gemini"
    return "litellm"


def resolve_adapter_class(model: str) -> Type[CoderAdapter]:
    """Resolve the appropriate coder adapter class for a model string.

    Args:
        model: Model identifier (e.g., "claude-sonnet-4-20250514", "gpt-4o")

    Returns:
        CoderAdapter subclass best suited for the model.
    """
    coder_name = resolve_coder_name(model=model)
    return CODER_REGISTRY[coder_name]


def get_coder(name: str, **config: Any) -> CoderAdapter:
    """Get a coder adapter by registry name.

    Args:
        name: Registered coder name (e.g., "litellm", "mock", "opencode-cli")
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
