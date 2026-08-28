"""Tests for custom OpenAI-compatible providers (e.g. Ollama Cloud).

FILE: tests/infrastructure/test_custom_provider.py
"""

from unittest.mock import patch, MagicMock

from snodo.config import ConfigManager, ProviderConfig
from snodo.infrastructure.model_discovery import discover_models, _discover_openai_compatible


def test_provider_config_fields():
    """ProviderConfig parses litellm_provider and extra_headers."""
    pc = ProviderConfig(
        litellm_provider="openai",
        base_url="https://ollama.com/v1",
        extra_headers={"X-Test-Header": "value"},
    )
    assert pc.litellm_provider == "openai"
    assert pc.base_url == "https://ollama.com/v1"
    assert pc.extra_headers == {"X-Test-Header": "value"}


def test_provider_for_model_and_litellm_decoupling(monkeypatch):
    """_provider_for_model returns config key while resolve_litellm_* returns litellm routing info."""
    custom_providers = {
        "ollama": ProviderConfig(
            litellm_provider="openai",
            base_url="https://ollama.com/v1",
            api_key_env="OLLAMA_API_KEY",
            extra_headers={"Custom-Affinity": "{task_id}"},
        )
    }
    monkeypatch.setattr(ConfigManager, "get_providers", lambda self: custom_providers)

    model = "ollama/llama-3.3-70b-instruct"

    assert ConfigManager._provider_for_model(model) == "ollama"
    assert ConfigManager.resolve_litellm_provider(model) == "openai"
    assert ConfigManager.resolve_litellm_model(model) == "openai/llama-3.3-70b-instruct"
    assert ConfigManager.resolve_api_base(model) == "https://ollama.com/v1"
    assert ConfigManager.resolve_extra_headers(model, task_id="task-999") == {"Custom-Affinity": "task-999"}


def test_discover_openai_compatible_generic():
    """_discover_openai_compatible fetches models from custom base_url or models_endpoint."""
    pc = ProviderConfig(
        base_url="https://ollama.com/v1",
        api_key="test-key",
    )

    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "object": "list",
        "data": [
            {"id": "llama-3.3-70b-instruct", "display_name": "Llama 3.3 70B"},
            {"id": "deepseek-r1-70b"},
        ],
    }
    fake_resp.raise_for_status.return_value = None

    with patch("httpx.get", return_value=fake_resp) as mock_get:
        models = _discover_openai_compatible(pc, provider_name="ollama")

    mock_get.assert_called_once()
    args, kwargs = mock_get.call_args
    assert args[0] == "https://ollama.com/v1/models"
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"

    assert len(models) == 2
    assert models[0].provider == "ollama"
    assert models[0].id == "llama-3.3-70b-instruct"
    assert models[0].full_string == "ollama/llama-3.3-70b-instruct"
    assert models[1].full_string == "ollama/deepseek-r1-70b"


def test_ollama_acceptance_without_openai_provider_block(monkeypatch):
    """Acceptance test: a provider block named 'ollama' without an 'openai' block lists models and runs completion."""
    custom_providers = {
        "ollama": ProviderConfig(
            litellm_provider="openai",
            base_url="https://ollama.com/v1",
            api_key="ollama-secret",
        )
    }
    # Ensure no 'openai' block exists in providers dict
    assert "openai" not in custom_providers

    monkeypatch.setattr(ConfigManager, "get_providers", lambda self: custom_providers)

    # 1. Model discovery works for 'ollama' without requiring an 'openai' block
    fake_models_resp = MagicMock()
    fake_models_resp.json.return_value = {"data": [{"id": "llama3.3"}]}
    fake_models_resp.raise_for_status.return_value = None

    with patch("httpx.get", return_value=fake_models_resp):
        discovered = discover_models(custom_providers, force_refresh=True)

    ollama_models = [m for m in discovered if m.provider == "ollama"]
    assert len(ollama_models) == 1
    assert ollama_models[0].full_string == "ollama/llama3.3"

    # 2. Execution routes completion to litellm with model='openai/llama3.3' and api_base='https://ollama.com/v1'
    from snodo.coders.litellm import LiteLLMAdapter
    from snodo.core.interfaces import TaskSpec

    adapter = LiteLLMAdapter(model="ollama/llama3.3")

    fake_choice = MagicMock()
    fake_choice.message.content = '[{"path": "hello.py", "content": "print(1)", "action": "write"}]'
    fake_completion_resp = MagicMock()
    fake_completion_resp.choices = [fake_choice]

    mock_completion = MagicMock(return_value=fake_completion_resp)
    adapter._completion_fn = mock_completion

    spec = TaskSpec(description="write hello.py", constraints=[])
    artifact = adapter.implement(spec)

    assert len(artifact.files) == 1
    assert artifact.files[0].path == "hello.py"

    mock_completion.assert_called_once()
    call_kwargs = mock_completion.call_args.kwargs
    assert call_kwargs["model"] == "openai/llama3.3"
    assert call_kwargs["api_base"] == "https://ollama.com/v1"
