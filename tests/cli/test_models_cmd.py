"""Tests for snodo models CLI command (models_cmd.py).

FILE: tests/cli/test_models_cmd.py
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import typer
from typer.testing import CliRunner

from snodo.cli.commands.models_cmd import (
    _apply_discrete_filters,
    _get_models,
    _lookup_context,
    _lookup_price,
    _read_cache,
    _write_cache,
    models_command,
    register,
)


@pytest.fixture
def mock_providers_config():
    """Mock ConfigManager providers."""
    class DummyProviderConfig:
        def __init__(self, api_key=None, api_key_env=None, base_url=None, models_endpoint=None, extra_headers=None):
            self.api_key = api_key
            self.api_key_env = api_key_env
            self.base_url = base_url
            self.models_endpoint = models_endpoint
            self.extra_headers = extra_headers

    return {
        "openai": DummyProviderConfig(api_key="sk-test-key"),
        "anthropic": DummyProviderConfig(api_key_env="ANTHROPIC_API_KEY"),
        "ollama": DummyProviderConfig(api_key="ollama-key", base_url="http://localhost:11434/v1"),
    }


# ============================================================================
# 1. Provider listing tests (_list_providers / models_command)
# ============================================================================

def test_models_command_list_providers_happy_path(mock_providers_config, monkeypatch, capsys):
    """models_command lists configured providers when keys are set."""
    monkeypatch.setattr("snodo.config.ConfigManager.get_providers", lambda self: mock_providers_config)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    args = SimpleNamespace(provider=None, flush=False)
    res = models_command(args)

    assert res == 0
    out = capsys.readouterr().out
    assert "Configured providers:" in out
    assert "openai" in out
    assert "anthropic" in out


def test_models_command_no_providers_configured(monkeypatch, capsys):
    """models_command informs user when no provider keys are configured."""
    monkeypatch.setattr("snodo.config.ConfigManager.get_providers", lambda self: {})

    args = SimpleNamespace(provider=None, flush=False)
    res = models_command(args)

    assert res == 0
    out = capsys.readouterr().out
    assert "No providers configured." in out


def test_models_command_unconfigured_provider_failure(mock_providers_config, monkeypatch, capsys):
    """models_command returns 1 when requested provider is not configured."""
    monkeypatch.setattr("snodo.config.ConfigManager.get_providers", lambda self: mock_providers_config)

    args = SimpleNamespace(provider="nonexistent", flush=False)
    res = models_command(args)

    assert res == 1
    err = capsys.readouterr().err
    assert "Provider not configured: nonexistent" in err


# ============================================================================
# 2. Model listing & discovery tests
# ============================================================================

def test_models_command_provider_models_happy_path(mock_providers_config, monkeypatch, capsys):
    """models_command lists discovered models for a provider."""
    monkeypatch.setattr("snodo.config.ConfigManager.get_providers", lambda self: mock_providers_config)

    sample_models = [
        {"id": "gpt-4o", "full_string": "openai/gpt-4o", "context_window": 128000},
        {"id": "gpt-3.5-turbo", "full_string": "openai/gpt-3.5-turbo", "context_window": 16384},
    ]
    monkeypatch.setattr("snodo.cli.commands.models_cmd._get_models", lambda p, pc, force_refresh: sample_models)

    args = SimpleNamespace(provider="openai", flush=False)
    res = models_command(args)

    assert res == 0
    out = capsys.readouterr().out
    assert "Provider: openai" in out
    assert "openai/gpt-4o" in out
    assert "2 model(s) from openai" in out


def test_models_command_no_models_discovered(mock_providers_config, monkeypatch, capsys):
    """models_command prints message when no models are discovered."""
    monkeypatch.setattr("snodo.config.ConfigManager.get_providers", lambda self: mock_providers_config)
    monkeypatch.setattr("snodo.cli.commands.models_cmd._get_models", lambda p, pc, force_refresh: [])

    args = SimpleNamespace(provider="openai", flush=False)
    res = models_command(args)

    assert res == 0
    out = capsys.readouterr().out
    assert "No models discovered for openai" in out


def test_generic_openai_compatible_discovery_stubbed(mock_providers_config, monkeypatch, tmp_path, capsys):
    """Generic OpenAI-compatible discovery path against a stubbed httpx endpoint (Ollama Cloud)."""
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_home", lambda: tmp_path)
    monkeypatch.setattr("snodo.cli.commands.models_cmd.resolve_home", lambda: tmp_path)
    monkeypatch.setattr("snodo.config.ConfigManager.get_providers", lambda self: mock_providers_config)
    monkeypatch.setattr("snodo.infrastructure.model_catalog.lookup", lambda name: {})

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": [
            {"id": "llama3:8b", "display_name": "Llama 3 8B", "context_window": 8192},
        ]
    }
    mock_response.raise_for_status = MagicMock()

    mock_httpx = MagicMock()
    mock_httpx.get.return_value = mock_response
    monkeypatch.setitem(__import__("sys").modules, "httpx", mock_httpx)

    args = SimpleNamespace(provider="ollama", flush=True)
    res = models_command(args)

    assert res == 0
    out = capsys.readouterr().out
    assert "ollama/llama3:8b" in out
    assert mock_httpx.get.called
    call_url = mock_httpx.get.call_args_list[0][0][0]
    assert call_url == "http://localhost:11434/v1/models"


def test_get_models_discovery_exception_fallback(mock_providers_config, monkeypatch, capsys):
    """_get_models prints error on discovery failure and falls back to cache."""
    pc = mock_providers_config["openai"]

    def failing_discover(pc):
        raise RuntimeError("Network timeout")

    monkeypatch.setitem(
        __import__("snodo.infrastructure.model_discovery", fromlist=["_DISCOVERY_DISPATCH"])._DISCOVERY_DISPATCH,
        "openai",
        failing_discover,
    )
    monkeypatch.setattr("snodo.cli.commands.models_cmd._read_cache", lambda p: [{"id": "cached-model", "full_string": "openai/cached-model"}])

    models = _get_models("openai", pc, force_refresh=True)

    assert len(models) == 1
    assert models[0]["id"] == "cached-model"
    err = capsys.readouterr().err
    assert "Discovery failed: Network timeout" in err


# ============================================================================
# 3. Cache reading & writing tests
# ============================================================================

def test_cache_write_and_read(tmp_path, monkeypatch):
    """_write_cache and _read_cache persist and read model discovery cache."""
    monkeypatch.setattr("snodo.cli.commands.models_cmd.resolve_home", lambda: tmp_path)
    monkeypatch.setattr("snodo.cli.commands.models_cmd._CACHE_DIR", tmp_path / "models")

    models_data = [{"id": "m1", "full_string": "prov/m1"}]
    _write_cache("prov", models_data)

    cached = _read_cache("prov")
    assert cached == models_data


def test_read_cache_expired_or_corrupt(tmp_path, monkeypatch):
    """_read_cache returns None when cache file is missing, expired, or corrupt."""
    monkeypatch.setattr("snodo.cli.commands.models_cmd.resolve_home", lambda: tmp_path)
    cache_dir = tmp_path / "models"
    monkeypatch.setattr("snodo.cli.commands.models_cmd._CACHE_DIR", cache_dir)

    # Missing
    assert _read_cache("missing") is None

    # Corrupt
    cache_dir.mkdir(parents=True, exist_ok=True)
    corrupt_file = cache_dir / "corrupt.json"
    corrupt_file.write_text("{invalid json")
    assert _read_cache("corrupt") is None


# ============================================================================
# 4. Discrete filtering tests
# ============================================================================

def test_apply_discrete_filters(monkeypatch):
    """_apply_discrete_filters filters models using shell-safe parameters."""
    models = [
        {"id": "gpt-4o", "display_name": "GPT-4o", "full_string": "openai/gpt-4o", "context_window": 128000},
        {"id": "gpt-3.5-turbo", "display_name": "GPT-3.5 Turbo", "full_string": "openai/gpt-3.5-turbo", "context_window": 16384},
        {"id": "claude-3-opus", "display_name": "Claude 3 Opus", "full_string": "anthropic/claude-3-opus", "context_window": 200000},
    ]

    # Filter by ID substring
    filtered = _apply_discrete_filters(models, id_contains="gpt")
    assert len(filtered) == 2

    # Filter by min context
    filtered_ctx = _apply_discrete_filters(models, min_context=100000)
    assert len(filtered_ctx) == 2
    assert {m["id"] for m in filtered_ctx} == {"gpt-4o", "claude-3-opus"}

    # Mock litellm costs
    mock_litellm = MagicMock()
    mock_litellm.model_cost = {
        "openai/gpt-4o": {"input_cost_per_token": 0.000005, "output_cost_per_token": 0.000015},
        "openai/gpt-3.5-turbo": {"input_cost_per_token": 0.0000015, "output_cost_per_token": 0.000002},
    }
    monkeypatch.setitem(__import__("sys").modules, "litellm", mock_litellm)

    # Max output cost <= $10 per 1M
    filtered_cost = _apply_discrete_filters(models, max_output_cost=10.0)
    assert len(filtered_cost) == 1
    assert filtered_cost[0]["id"] == "gpt-3.5-turbo"


def test_models_command_no_filters_matched(mock_providers_config, monkeypatch, capsys):
    """models_command prints message when filters exclude all models."""
    monkeypatch.setattr("snodo.config.ConfigManager.get_providers", lambda self: mock_providers_config)
    sample_models = [{"id": "gpt-4o", "full_string": "openai/gpt-4o", "context_window": 128000}]
    monkeypatch.setattr("snodo.cli.commands.models_cmd._get_models", lambda p, pc, force_refresh: sample_models)

    args = SimpleNamespace(
        provider="openai",
        flush=False,
        id_contains="nonexistent_model",
        max_output_cost=None,
        min_output_cost=None,
        max_input_cost=None,
        min_context=None,
    )
    res = models_command(args)

    assert res == 0
    assert "No models matched the specified filters." in capsys.readouterr().out


# ============================================================================
# 5. Lookup & Typer registration tests
# ============================================================================

def test_lookup_price_and_context(monkeypatch):
    """_lookup_price and _lookup_context query catalog for metadata."""
    mock_lookup = MagicMock(return_value={"input_cost": 0.000005, "output_cost": 0.000015, "context": 128000})
    monkeypatch.setattr("snodo.infrastructure.model_catalog.lookup", mock_lookup)

    inp, outp = _lookup_price("openai/gpt-4o")
    assert inp == "$5.00"
    assert outp == "$15.00"

    ctx_str = _lookup_context("openai/gpt-4o")
    assert ctx_str == "128000"


def test_cli_register_models(mock_providers_config, monkeypatch):
    """Typer app registration exposes models command."""
    app = typer.Typer()
    register(app)

    runner = CliRunner()
    monkeypatch.setattr("snodo.config.ConfigManager.get_providers", lambda self: mock_providers_config)

    res = runner.invoke(app, [])
    assert res.exit_code == 0
