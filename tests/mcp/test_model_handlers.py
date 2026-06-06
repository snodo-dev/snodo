"""Tests for model discovery MCP tools (mcp/model_handlers.py).

All discover_models calls mocked — no live network.
"""

from unittest.mock import patch

import pytest

from snodo.mcp.model_handlers import ModelToolHandler
from snodo.mcp.server import MCPError


def _make_model(provider: str, full_string: str, id_str: str = "") -> dict:
    return {
        "provider": provider,
        "id": id_str or full_string,
        "full_string": full_string,
        "display_name": full_string,
        "context_window": 0,
    }


@pytest.fixture
def handler():
    return ModelToolHandler()


def _patch_discover(return_models: list):
    """Patch discover_models to return a fixed list of ModelInfo dicts."""
    from snodo.infrastructure.model_discovery import ModelInfo
    model_objects = [ModelInfo(**m) for m in return_models]
    return patch(
        "snodo.mcp.model_handlers.discover_models",
        return_value=model_objects,
    )


class TestListModels:
    def test_returns_all_models(self, handler):
        with _patch_discover([
            _make_model("anthropic", "claude-sonnet-4-20250514"),
            _make_model("google", "gemini/gemini-2.0-flash-exp"),
        ]):
            result = handler.handle_list_models({})

        assert len(result["models"]) == 2
        assert result["models"][0]["provider"] == "anthropic"
        assert result["models"][1]["provider"] == "google"

    def test_provider_filter_narrows_results(self, handler):
        with _patch_discover([
            _make_model("anthropic", "claude-sonnet-4-20250514"),
            _make_model("google", "gemini/gemini-2.0-flash-exp"),
        ]):
            result = handler.handle_list_models({"provider": "anthropic"})

        assert len(result["models"]) == 1
        assert result["models"][0]["provider"] == "anthropic"

    def test_provider_filter_no_match_returns_empty(self, handler):
        with _patch_discover([
            _make_model("anthropic", "claude-sonnet-4-20250514"),
        ]):
            result = handler.handle_list_models({"provider": "openai"})

        assert len(result["models"]) == 0


class TestResolveModel:
    def test_exact_match(self, handler):
        with _patch_discover([
            _make_model("openai", "gpt-4o"),
        ]):
            result = handler.handle_resolve_model({"query": "gpt4o"})

        assert result["status"] == "exact"
        assert result["model"]["full_string"] == "gpt-4o"

    def test_not_found(self, handler):
        with _patch_discover([
            _make_model("openai", "gpt-4o"),
        ]):
            result = handler.handle_resolve_model({"query": "nonexistent"})

        assert result["status"] == "not_found"
        assert result["query"] == "nonexistent"

    def test_empty_query_raises(self, handler):
        with pytest.raises(MCPError, match="requires query"):
            handler.handle_resolve_model({})

    def test_ambiguous_no_index_returns_candidates(self, handler):
        with _patch_discover([
            _make_model("google", "gemini/gemini-2.0-flash-exp"),
            _make_model("openrouter", "google/gemini-2.0-flash-exp"),
        ]):
            result = handler.handle_resolve_model({"query": "gemini"})

        assert result["status"] == "ambiguous"
        assert len(result["candidates"]) == 2
        assert "hint" in result
        assert "index" in result["hint"].lower()

    def test_ambiguous_with_valid_index_resolves(self, handler):
        with _patch_discover([
            _make_model("google", "gemini/gemini-2.0-flash-exp"),
            _make_model("openrouter", "google/gemini-2.0-flash-exp"),
        ]):
            result = handler.handle_resolve_model({"query": "gemini", "index": 1})

        assert result["status"] == "exact"
        assert result["model"]["provider"] == "openrouter"

    def test_ambiguous_index_zero_resolves_first(self, handler):
        with _patch_discover([
            _make_model("google", "gemini/gemini-2.0-flash-exp"),
            _make_model("openrouter", "google/gemini-2.0-flash-exp"),
        ]):
            result = handler.handle_resolve_model({"query": "gemini", "index": 0})

        assert result["status"] == "exact"
        assert result["model"]["provider"] == "google"

    def test_ambiguous_out_of_range_index_raises(self, handler):
        with _patch_discover([
            _make_model("google", "gemini/gemini-2.0-flash-exp"),
            _make_model("openrouter", "google/gemini-2.0-flash-exp"),
        ]):
            with pytest.raises(MCPError, match="out of range"):
                handler.handle_resolve_model({"query": "gemini", "index": 5})
