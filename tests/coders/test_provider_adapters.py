"""Tests for provider-specific coder adapters."""

import pytest

from snodo.coders import (
    resolve_adapter_class,
    OpenAIAdapter,
    AnthropicAdapter,
    GeminiAdapter,
    LiteLLMAdapter,
    MockAdapter,
)
from snodo.coders.litellm import _truncated_log


class TestResolveAdapterClass:
    """resolve_adapter_class routes by model prefix."""

    def test_openai_gpt(self):
        assert resolve_adapter_class("gpt-4o") is OpenAIAdapter
        assert resolve_adapter_class("gpt-4o-mini") is OpenAIAdapter
        assert resolve_adapter_class("gpt-4") is OpenAIAdapter

    def test_openai_o1(self):
        assert resolve_adapter_class("o1-mini") is OpenAIAdapter
        assert resolve_adapter_class("o1-preview") is OpenAIAdapter

    def test_openai_o3(self):
        assert resolve_adapter_class("o3-mini") is OpenAIAdapter
        assert resolve_adapter_class("o3") is OpenAIAdapter
        assert resolve_adapter_class("o3-pro-2025-06-10") is OpenAIAdapter

    def test_anthropic_claude(self):
        assert resolve_adapter_class("claude-sonnet-4-20250514") is AnthropicAdapter
        assert resolve_adapter_class("claude-3-5-sonnet") is AnthropicAdapter
        assert resolve_adapter_class("claude-haiku") is AnthropicAdapter

    def test_gemini_prefix(self):
        assert resolve_adapter_class("gemini-2.5-pro") is GeminiAdapter
        assert resolve_adapter_class("gemini/gemini-2.0-flash") is GeminiAdapter

    def test_google_prefix(self):
        assert resolve_adapter_class("google/gemini-2.5-pro") is GeminiAdapter

    def test_fallback_litellm(self):
        assert resolve_adapter_class("deepseek/deepseek-chat") is LiteLLMAdapter
        assert resolve_adapter_class("openrouter/qwen/qwen3-coder") is LiteLLMAdapter
        assert resolve_adapter_class("unknown-model") is LiteLLMAdapter


class TestTruncationReasons:
    """Each adapter has correct TRUNCATION_REASONS."""

    def test_openai_truncation(self):
        assert OpenAIAdapter.TRUNCATION_REASONS == {"length"}

    def test_anthropic_truncation(self):
        assert AnthropicAdapter.TRUNCATION_REASONS == {"max_tokens"}

    def test_gemini_truncation(self):
        assert GeminiAdapter.TRUNCATION_REASONS == {"MAX_TOKENS"}

    def test_base_truncation(self):
        assert LiteLLMAdapter.TRUNCATION_REASONS == {"length"}


class TestCheckTruncation:
    """_check_truncation detects provider-specific truncation reasons."""

    def _make_response(self, finish_reason: str, content: str = "truncated"):
        """Create a mock response object."""
        from unittest.mock import MagicMock
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].finish_reason = finish_reason
        response.choices[0].message.content = content
        return response

    def test_openai_detects_length(self):
        adapter = OpenAIAdapter(model="gpt-4o")
        response = self._make_response("length")
        with pytest.raises(Exception):  # ParseError
            adapter._check_truncation(response)

    def test_openai_ignores_max_tokens(self):
        adapter = OpenAIAdapter(model="gpt-4o")
        response = self._make_response("max_tokens")
        adapter._check_truncation(response)  # Should not raise

    def test_anthropic_detects_max_tokens(self):
        adapter = AnthropicAdapter(model="claude-sonnet-4-20250514")
        response = self._make_response("max_tokens")
        with pytest.raises(Exception):  # ParseError
            adapter._check_truncation(response)

    def test_anthropic_ignores_length(self):
        adapter = AnthropicAdapter(model="claude-sonnet-4-20250514")
        response = self._make_response("length")
        adapter._check_truncation(response)  # Should not raise

    def test_gemini_detects_MAX_TOKENS(self):
        adapter = GeminiAdapter(model="gemini-2.5-pro")
        response = self._make_response("MAX_TOKENS")
        with pytest.raises(Exception):  # ParseError
            adapter._check_truncation(response)

    def test_gemini_ignores_length(self):
        adapter = GeminiAdapter(model="gemini-2.5-pro")
        response = self._make_response("length")
        adapter._check_truncation(response)  # Should not raise

    def test_gemini_ignores_lowercase_max_tokens(self):
        adapter = GeminiAdapter(model="gemini-2.5-pro")
        response = self._make_response("max_tokens")
        adapter._check_truncation(response)  # Should not raise (case-sensitive)


class TestAdapterInheritance:
    """Subclasses inherit base behavior."""

    def test_openai_inherits_implement(self):
        adapter = OpenAIAdapter(model="gpt-4o")
        assert hasattr(adapter, "implement")
        assert hasattr(adapter, "_build_prompt")
        assert hasattr(adapter, "_parse_response")

    def test_anthropic_inherits_implement(self):
        adapter = AnthropicAdapter(model="claude-sonnet-4-20250514")
        assert hasattr(adapter, "implement")
        assert hasattr(adapter, "_call_llm_with_tools")

    def test_gemini_inherits_implement(self):
        adapter = GeminiAdapter(model="gemini-2.5-pro")
        assert hasattr(adapter, "implement")
        assert hasattr(adapter, "_call_llm_with_tools")

    def test_openai_uses_base_tool_loop(self):
        """OpenAIAdapter uses the same _call_llm_with_tools as base."""
        assert OpenAIAdapter._call_llm_with_tools is LiteLLMAdapter._call_llm_with_tools

    def test_anthropic_uses_base_tool_loop(self):
        """AnthropicAdapter uses the same _call_llm_with_tools as base."""
        assert AnthropicAdapter._call_llm_with_tools is LiteLLMAdapter._call_llm_with_tools

    def test_gemini_uses_base_tool_loop(self):
        """GeminiAdapter uses the same _call_llm_with_tools as base."""
        assert GeminiAdapter._call_llm_with_tools is LiteLLMAdapter._call_llm_with_tools
