"""Coder adapter registry and installed coder plugin discovery.

FILE: snodo/coders/__init__.py

Registry pattern for pluggable coder backends.
"""

import logging
from importlib.metadata import entry_points
from typing import Any, Dict, Optional, Type

from snodo.coders.base import (
    CoderAdapter,
    AdapterError as AdapterError,
    CoderTimeoutError as CoderTimeoutError,
    CoderUnavailableError as CoderUnavailableError,
    LLMCallError as LLMCallError,
    ParseError as ParseError,
    TurnBudgetExhausted as TurnBudgetExhausted,
)
from snodo.coders.availability import check_coder_available as check_coder_available
from snodo.coders.inert_settings import explicit_coder_settings as explicit_coder_settings
from snodo.coders.inert_settings import report_inert_coder_settings as report_inert_coder_settings
from snodo.coders.inert_settings import reset_reported_inert_settings as reset_reported_inert_settings
from snodo.coders.litellm import LiteLLMAdapter
from snodo.coders.mock import MockAdapter
from snodo.coders.openai_adapter import OpenAIAdapter
from snodo.coders.anthropic_adapter import AnthropicAdapter
from snodo.coders.gemini_adapter import GeminiAdapter
from snodo.coders.opencode_adapter import OpenCodeAdapter
from snodo.coders.opencode_cli_adapter import OpenCodeCLIAdapter
from snodo.coders.agy_adapter import AGYAdapter
from snodo.coders.codex_cli_adapter import CodexCLIAdapter
from snodo.coders.claude_cli_adapter import ClaudeCLIAdapter
from snodo.infrastructure.config import DEFAULT_MODEL

_logger = logging.getLogger(__name__)

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
    "codex-cli": CodexCLIAdapter,
    "claude-cli": ClaudeCLIAdapter,
}

_BUILTIN_CODER_NAMES = frozenset(CODER_REGISTRY)
_PLUGIN_LOAD_FAILURES: Dict[str, str] = {}


def discover_coder_plugins() -> None:
    """Load installed ``snodo.coders`` entry points into the coder registry.

    Entry-point names are selectable coder names. Invalid adapters and import
    failures are retained for callers that report plugin readiness and never
    prevent Snodo from starting.
    """
    _PLUGIN_LOAD_FAILURES.clear()
    try:
        plugins = entry_points(group="snodo.coders")
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        _PLUGIN_LOAD_FAILURES["*"] = message
        _logger.warning("Could not discover snodo.coders entry points: %s", message)
        return

    for plugin in plugins:
        try:
            adapter_cls = plugin.load()
            if not isinstance(adapter_cls, type) or not issubclass(adapter_cls, CoderAdapter):
                raise TypeError("entry point must load a CoderAdapter subclass")
            if plugin.name in _BUILTIN_CODER_NAMES:
                raise ValueError(f"coder name '{plugin.name}' is reserved by a built-in coder")
            CODER_REGISTRY[plugin.name] = adapter_cls
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            _PLUGIN_LOAD_FAILURES[plugin.name] = message
            _logger.warning("Could not load coder plugin '%s': %s", plugin.name, message)


def coder_plugin_load_failures() -> Dict[str, str]:
    """Return a copy of coder plugin discovery and load failures."""
    return _PLUGIN_LOAD_FAILURES.copy()


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
    4. Model string prefix mapping (claude-cli/, codex-cli/, opencode-cli/, opencode/, agy/, gpt/o1/o3, claude, gemini)
    5. Default fallback ('litellm')
    """
    if use_mock:
        return "mock"
    if cli_coder:
        return cli_coder
    if mode_coder:
        return mode_coder
    if model:
        if model.startswith("claude-cli/"):
            return "claude-cli"
        if model.startswith("codex-cli/"):
            return "codex-cli"
        if model.startswith("opencode-cli/"):
            return "opencode-cli"
        if model.startswith("opencode/"):
            return "opencode"
        if model.startswith("agy/"):
            return "agy"
        # Built-in routes above retain priority; installed plugins may opt in
        # to model routing by using their registered name as a prefix.
        for coder_name in CODER_REGISTRY:
            if coder_name not in _BUILTIN_CODER_NAMES and model.startswith(f"{coder_name}/"):
                return coder_name
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


discover_coder_plugins()
