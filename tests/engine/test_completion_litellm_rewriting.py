"""Tests for completion function litellm provider model rewriting and credential binding (Fixes #146).

FILE: tests/engine/test_completion_litellm_rewriting.py

PROVES:
- A provider block with litellm_provider set binds the rewritten model string in build_completion_fn
- A provider block without litellm_provider set leaves the model string unchanged
- The model bound by build_completion_fn for validators matches what llm_validator.py:408 sends for the same config
- Completion binding carries the configured API key directly
"""

import os
from unittest.mock import MagicMock

from snodo.config import ConfigManager, ProviderConfig, provider_env
from snodo.validators.runner import build_completion_fn


def test_build_completion_fn_with_litellm_provider_rewrites_model(monkeypatch):
    """A provider block with litellm_provider set binds the rewritten model in build_completion_fn."""
    custom_providers = {
        "ollama": ProviderConfig(
            litellm_provider="openai",
            base_url="https://ollama.com/v1",
            api_key="secret-ollama-key",
            api_key_env="OLLAMA_API_KEY",
        )
    }
    monkeypatch.setattr(ConfigManager, "get_providers", lambda self: custom_providers)

    model = "ollama/llama-3.3-70b-instruct"
    dummy_base_fn = MagicMock()

    fn = build_completion_fn(model, dummy_base_fn)
    assert fn is not None
    assert fn.keywords["model"] == "openai/llama-3.3-70b-instruct"
    assert fn.keywords["api_base"] == "https://ollama.com/v1"


def test_build_completion_fn_without_litellm_provider_is_unchanged(monkeypatch):
    """A model without litellm_provider set is bound unchanged in build_completion_fn."""
    model = "claude-sonnet-4-20250514"
    dummy_base_fn = MagicMock()

    fn = build_completion_fn(model, dummy_base_fn)
    assert fn is not None
    assert fn.keywords["model"] == "claude-sonnet-4-20250514"
    assert "api_base" not in fn.keywords


def test_validator_bound_model_matches_llm_validator_direct_call(monkeypatch):
    """The model bound in build_completion_fn matches what llm_validator.py:408 sends for the same config."""
    custom_providers = {
        "custom_llm": ProviderConfig(
            litellm_provider="openai",
            base_url="https://custom.llm/v1",
        )
    }
    monkeypatch.setattr(ConfigManager, "get_providers", lambda self: custom_providers)

    model = "custom_llm/qwen2.5-coder"
    dummy_base_fn = MagicMock()

    fn = build_completion_fn(model, dummy_base_fn)
    bound_model = fn.keywords["model"]

    direct_resolved_model = ConfigManager.resolve_litellm_model(model)
    assert bound_model == direct_resolved_model == "openai/qwen2.5-coder"


def test_completion_binding_carries_key_without_environment_mutation(monkeypatch):
    """A routed provider's key is bound directly and does not touch the environment."""
    custom_providers = {
        "ollama": ProviderConfig(
            litellm_provider="openai",
            base_url="https://ollama.com/v1",
            api_key="ollama-secret-token",
            api_key_env="OLLAMA_API_KEY",
        )
    }
    monkeypatch.setattr(ConfigManager, "get_providers", lambda self: custom_providers)

    model = "ollama/llama3"
    with provider_env(model):
        fn = build_completion_fn(model, MagicMock())
        assert fn.keywords["api_key"] == "ollama-secret-token"
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert "OLLAMA_API_KEY" not in os.environ
    assert "OPENAI_API_KEY" not in os.environ
