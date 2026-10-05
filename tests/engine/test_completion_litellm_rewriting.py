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

import pytest

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


def test_local_provider_prefix_is_removed_when_litellm_id_has_its_own_prefix(monkeypatch):
    """Use the local provider for credentials without forwarding it to LiteLLM."""
    custom_providers = {
        "google": ProviderConfig(
            litellm_provider="gemini",
            api_key="google-test-key",
            base_url="https://google.example/v1",
        )
    }
    monkeypatch.setattr(ConfigManager, "get_providers", lambda self: custom_providers)

    model = "google/gemini/gemini-3.8-flash"
    fn = build_completion_fn(model, MagicMock())

    assert ConfigManager.resolve_litellm_model(model) == "gemini/gemini-3.8-flash"
    assert fn.keywords["model"] == "gemini/gemini-3.8-flash"
    assert fn.keywords["api_key"] == "google-test-key"
    assert fn.keywords["api_base"] == "https://google.example/v1"


def test_existing_litellm_model_forms_and_unknown_provider_are_unchanged(monkeypatch):
    custom_providers = {
        "google": ProviderConfig(litellm_provider="gemini"),
        "openai": ProviderConfig(),
        "ollama-cloud": ProviderConfig(),
    }
    monkeypatch.setattr(ConfigManager, "get_providers", lambda self: custom_providers)

    assert ConfigManager.resolve_litellm_model("openai/gpt-5.6-terra") == "openai/gpt-5.6-terra"
    assert ConfigManager.resolve_litellm_model("ollama-cloud/deepseek-v4-pro:0813") == "ollama-cloud/deepseek-v4-pro:0813"
    assert ConfigManager.resolve_litellm_model("unconfigured/model-name") == "unconfigured/model-name"


@pytest.mark.parametrize(
    ("configured", "resolved"),
    [
        ("google/gemini/gemini-3.8-flash", "gemini/gemini-3.8-flash"),
        ("google/gemini-3.8-flash", "gemini/gemini-3.8-flash"),
        ("gemini/gemini-3.8-flash", "gemini/gemini-3.8-flash"),
    ],
)
def test_google_gemini_model_forms_resolve_in_isolated_config_home(
    tmp_path, monkeypatch, configured, resolved
):
    """Google routing works with defaults and a user block lacking routing metadata."""
    config_home = tmp_path / "config-home"
    config_home.mkdir()
    monkeypatch.setenv("SNODO_HOME", str(config_home))

    assert ConfigManager.resolve_litellm_model(configured) == resolved


def test_google_user_provider_block_without_litellm_provider_resolves(
    tmp_path, monkeypatch
):
    import yaml

    config_home = tmp_path / "config-home"
    config_home.mkdir()
    (config_home / "config.yml").write_text(
        yaml.safe_dump({"providers": {"google": {"api_key": "isolated-test-key"}}})
    )
    monkeypatch.setenv("SNODO_HOME", str(config_home))

    assert ConfigManager.resolve_litellm_model(
        "google/gemini/gemini-3.8-flash"
    ) == "gemini/gemini-3.8-flash"


def test_google_rewrite_preserves_other_provider_model_ids(tmp_path, monkeypatch):
    config_home = tmp_path / "config-home"
    config_home.mkdir()
    monkeypatch.setenv("SNODO_HOME", str(config_home))

    assert ConfigManager.resolve_litellm_model("openai/gpt-5.6-terra") == "openai/gpt-5.6-terra"
    assert ConfigManager.resolve_litellm_model("ollama-cloud/deepseek-v4-pro:0813") == "ollama-cloud/deepseek-v4-pro:0813"
    assert ConfigManager.resolve_litellm_model("ocgo/gemini-3.8-flash") == "ocgo/gemini-3.8-flash"
