"""Tests for model discovery (infrastructure/model_discovery.py).

All HTTP calls mocked — no live network.
"""

import json
import time
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from snodo.infrastructure.config import ProviderConfig
from snodo.infrastructure.model_discovery import (
    _discover_anthropic,
    _discover_openrouter,
    _discover_google,
    discover_models,
    _CACHE_TTL_SECONDS,
    _write_cache,
    _read_cache,
)


# === Fixtures ===

@pytest.fixture
def anthropic_cfg():
    return ProviderConfig(
        api_key_env="ANTHROPIC_API_KEY",
        models_endpoint="https://api.anthropic.com/v1/models",
    )


@pytest.fixture
def openrouter_cfg():
    return ProviderConfig(
        api_key_env="OPENROUTER_API_KEY",
        models_endpoint="https://openrouter.ai/api/v1/models",
    )


@pytest.fixture
def google_cfg():
    return ProviderConfig(
        api_key_env="GEMINI_API_KEY",
        models_endpoint="https://generativelanguage.googleapis.com/v1beta/models",
    )


@pytest.fixture
def temp_cache_dir(monkeypatch):
    """Use a temp directory for model cache."""
    d = tempfile.mkdtemp()
    monkeypatch.setattr(
        "snodo.infrastructure.model_discovery._cache_path",
        lambda: Path(d) / "model_cache.json",
    )
    return Path(d)


# === Provider-level tests ===

class TestDiscoverAnthropic:
    def test_returns_models(self, anthropic_cfg):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {"id": "claude-sonnet-4-20250514", "display_name": "Claude Sonnet 4"},
                {"id": "claude-3-5-haiku-20241022", "display_name": "Claude 3.5 Haiku"},
            ]
        }
        mock_resp.raise_for_status.return_value = None

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            with patch("httpx.get", return_value=mock_resp) as mock_get:
                results = _discover_anthropic(anthropic_cfg)

        assert len(results) == 2
        assert results[0].provider == "anthropic"
        assert results[0].id == "claude-sonnet-4-20250514"
        assert results[0].display_name == "Claude Sonnet 4"
        assert results[1].id == "claude-3-5-haiku-20241022"

        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["headers"]["X-Api-Key"] == "sk-ant-test"
        assert call_kwargs["headers"]["anthropic-version"] == "2023-06-01"

    def test_no_api_key_returns_empty(self, anthropic_cfg):
        with patch.dict("os.environ", {}, clear=True):
            results = _discover_anthropic(anthropic_cfg)
        assert results == []

    def test_http_error_returns_empty(self, anthropic_cfg):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            with patch("httpx.get", side_effect=Exception("Connection refused")):
                results = _discover_anthropic(anthropic_cfg)
        assert results == []


class TestDiscoverOpenRouter:
    def test_returns_models(self, openrouter_cfg):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {"id": "openai/gpt-4o", "name": "GPT-4o"},
                {"id": "anthropic/claude-sonnet-4-20250514", "extra": "ignored"},
            ]
        }
        mock_resp.raise_for_status.return_value = None

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test"}):
            with patch("httpx.get", return_value=mock_resp) as mock_get:
                results = _discover_openrouter(openrouter_cfg)

        assert len(results) == 2
        assert results[0].provider == "openrouter"
        assert results[0].id == "openai/gpt-4o"
        assert results[1].full_string == "anthropic/claude-sonnet-4-20250514"

        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["headers"]["Authorization"] == "Bearer sk-or-test"


class TestDiscoverGoogle:
    def test_returns_models_strips_prefix(self, google_cfg):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "models": [
                {"name": "models/gemini-2.0-flash-exp",
                 "displayName": "Gemini 2.0 Flash",
                 "inputTokenLimit": 1048576},
                {"name": "models/gemini-2.5-pro-exp-03-25",
                 "displayName": "Gemini 2.5 Pro",
                 "inputTokenLimit": 1048576},
            ]
        }
        mock_resp.raise_for_status.return_value = None

        with patch.dict("os.environ", {"GEMINI_API_KEY": "g-key"}):
            with patch("httpx.get", return_value=mock_resp) as mock_get:
                results = _discover_google(google_cfg)

        assert len(results) == 2
        assert results[0].id == "gemini-2.0-flash-exp"  # "models/" stripped
        assert results[0].full_string == "gemini/gemini-2.0-flash-exp"
        assert results[0].context_window == 1048576
        assert results[1].id == "gemini-2.5-pro-exp-03-25"

        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["params"]["key"] == "g-key"

    def test_no_models_prefix(self, google_cfg):
        """A model name without 'models/' prefix keeps its full name."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "models": [{"name": "palm2", "displayName": "PaLM 2"}]
        }
        mock_resp.raise_for_status.return_value = None

        with patch.dict("os.environ", {"GEMINI_API_KEY": "g-key"}):
            with patch("httpx.get", return_value=mock_resp):
                results = _discover_google(google_cfg)

        assert len(results) == 1
        assert results[0].id == "palm2"


# === Cache tests ===

class TestCache:
    def test_read_cache_hit_within_ttl(self, temp_cache_dir):
        models = [
            {"provider": "anthropic", "id": "claude-1", "full_string": "claude-1", "display_name": "", "context_window": 0},
        ]
        _write_cache(models)

        result = _read_cache()
        assert result is not None
        assert len(result) == 1
        assert result[0]["id"] == "claude-1"

    def test_read_cache_expired(self):
        """Cache with expired TTL returns None."""
        with tempfile.TemporaryDirectory() as d:
            cache_path = Path(d) / "model_cache.json"
            with patch("snodo.infrastructure.model_discovery._cache_path", return_value=cache_path):
                models = [{"provider": "a", "id": "old", "full_string": "old", "display_name": "", "context_window": 0}]
                _write_cache(models)

                # Rewrite with old timestamp
                payload = {"timestamp": time.time() - _CACHE_TTL_SECONDS - 10, "models": models}
                cache_path.write_text(json.dumps(payload))

                result = _read_cache()
                assert result is None  # expired

    def test_read_cache_missing_file(self, temp_cache_dir):
        result = _read_cache()
        assert result is None


class TestDiscoverModels:
    def test_cache_hit_no_http(self, temp_cache_dir):
        models = [
            {"provider": "anthropic", "id": "cached-1", "full_string": "cached-1", "display_name": "", "context_window": 0},
        ]
        _write_cache(models)

        with patch("httpx.get") as mock_get:
            result = discover_models({})

        assert len(result) == 1
        assert result[0].id == "cached-1"
        mock_get.assert_not_called()

    def test_force_refresh_bypasses_cache(self, temp_cache_dir):
        models = [
            {"provider": "anthropic", "id": "stale", "full_string": "stale", "display_name": "", "context_window": 0},
        ]
        _write_cache(models)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": [{"id": "fresh-model"}]}
        mock_resp.raise_for_status.return_value = None

        providers = {"anthropic": ProviderConfig(api_key_env="ANTHROPIC_API_KEY", models_endpoint="https://x.com")}
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk"}):
            with patch("httpx.get", return_value=mock_resp):
                result = discover_models(providers, force_refresh=True)

        assert len(result) == 1
        assert result[0].id == "fresh-model"

    def test_provider_failure_does_not_raise(self, temp_cache_dir):
        providers = {
            "anthropic": ProviderConfig(api_key_env="ANTHROPIC_API_KEY", models_endpoint="https://x.com"),
        }
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk"}):
            with patch("httpx.get", side_effect=Exception("network down")):
                result = discover_models(providers, force_refresh=True)

        # Returns empty list for fresh cache miss with all providers failing
        assert result == []

    def test_unknown_provider_skipped(self, temp_cache_dir):
        providers = {"unknown_provider": ProviderConfig()}
        with patch("httpx.get") as mock_get:
            result = discover_models(providers, force_refresh=True)

        assert result == []
        mock_get.assert_not_called()

    def test_provider_config_importable_from_infrastructure(self):
        """ProviderConfig is importable from infrastructure.config."""
        from snodo.infrastructure.config import ProviderConfig, DEFAULT_PROVIDER_CATALOG
        assert ProviderConfig is not None
        assert len(DEFAULT_PROVIDER_CATALOG) == 4
