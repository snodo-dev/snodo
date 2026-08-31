"""Tests for completion function litellm provider model rewriting and credential binding (Fixes #146).

FILE: tests/engine/test_completion_litellm_rewriting.py

PROVES:
- A provider block with litellm_provider set binds the rewritten model string in _build_completion_fn
- A provider block without litellm_provider set leaves the model string unchanged
- The model bound by _build_completion_fn for validators matches what llm_validator.py:408 sends for the same config
- provider_env sets the API key in both the block's api_key_env and the target litellm_provider's api_key_env
"""

import os
from unittest.mock import MagicMock

from snodo.config import ConfigManager, ProviderConfig, provider_env
from snodo.engine.loop import _build_completion_fn


def test_build_completion_fn_with_litellm_provider_rewrites_model(monkeypatch):
    """A provider block with litellm_provider set binds the rewritten model in _build_completion_fn."""
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

    fn = _build_completion_fn(model, dummy_base_fn)
    assert fn is not None
    assert fn.keywords["model"] == "openai/llama-3.3-70b-instruct"
    assert fn.keywords["api_base"] == "https://ollama.com/v1"


def test_build_completion_fn_without_litellm_provider_is_unchanged(monkeypatch):
    """A model without litellm_provider set is bound unchanged in _build_completion_fn."""
    model = "claude-sonnet-4-20250514"
    dummy_base_fn = MagicMock()

    fn = _build_completion_fn(model, dummy_base_fn)
    assert fn is not None
    assert fn.keywords["model"] == "claude-sonnet-4-20250514"
    assert "api_base" not in fn.keywords


def test_validator_bound_model_matches_llm_validator_direct_call(monkeypatch):
    """The model bound in _build_completion_fn matches what llm_validator.py:408 sends for the same config."""
    custom_providers = {
        "custom_llm": ProviderConfig(
            litellm_provider="openai",
            base_url="https://custom.llm/v1",
        )
    }
    monkeypatch.setattr(ConfigManager, "get_providers", lambda self: custom_providers)

    model = "custom_llm/qwen2.5-coder"
    dummy_base_fn = MagicMock()

    fn = _build_completion_fn(model, dummy_base_fn)
    bound_model = fn.keywords["model"]

    direct_resolved_model = ConfigManager.resolve_litellm_model(model)
    assert bound_model == direct_resolved_model == "openai/qwen2.5-coder"


def test_provider_env_binds_resolved_provider_credential_env_var(monkeypatch):
    """provider_env sets key in both block api_key_env and resolved litellm_provider api_key_env."""
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
        assert os.environ.get("OLLAMA_API_KEY") == "ollama-secret-token"
        assert os.environ.get("OPENAI_API_KEY") == "ollama-secret-token"
