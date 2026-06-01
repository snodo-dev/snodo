"""Tests for snodo.coders module.

FILE: tests/coders/test_coders.py

Tests cover:
- Registry pattern (get_coder, CODER_REGISTRY)
- CoderAdapter base class
- LiteLLMAdapter (renamed from BasicCoderAdapter)
- MockAdapter (renamed from MockCoderAdapter)
- Backward-compatible aliases
- Mode model coder fields
- create_coder factory
"""

import pytest
from unittest.mock import Mock

from snodo.core.interfaces import TaskSpec, FileArtifact, MCPServer, Coder


# ========== BASE / ABC TESTS ==========

def test_coder_adapter_is_coder_abc():
    """CoderAdapter is an alias for the Coder ABC."""
    from snodo.coders.base import CoderAdapter
    assert CoderAdapter is Coder


def test_exception_hierarchy():
    """Exception classes have correct hierarchy."""
    from snodo.coders.base import AdapterError, LLMCallError, ParseError
    assert issubclass(LLMCallError, AdapterError)
    assert issubclass(ParseError, AdapterError)
    assert issubclass(AdapterError, Exception)


# ========== REGISTRY TESTS ==========

def test_registry_contains_litellm():
    """Registry includes litellm adapter."""
    from snodo.coders import CODER_REGISTRY
    assert "litellm" in CODER_REGISTRY


def test_registry_contains_mock():
    """Registry includes mock adapter."""
    from snodo.coders import CODER_REGISTRY
    assert "mock" in CODER_REGISTRY


def test_get_coder_litellm():
    """get_coder returns LiteLLMAdapter for 'litellm'."""
    from snodo.coders import get_coder, LiteLLMAdapter
    coder = get_coder("litellm", model="gpt-4")
    assert isinstance(coder, LiteLLMAdapter)
    assert coder.model == "gpt-4"


def test_get_coder_mock():
    """get_coder returns MockAdapter for 'mock'."""
    from snodo.coders import get_coder, MockAdapter
    coder = get_coder("mock")
    assert isinstance(coder, MockAdapter)


def test_get_coder_mock_with_config():
    """get_coder passes config to MockAdapter."""
    from snodo.coders import get_coder
    coder = get_coder("mock", mock_files=[FileArtifact(path="src/main.py", content="print('hi')")])
    spec = TaskSpec(description="test", constraints=[])
    result = coder.implement(spec)
    assert result.files[0].content == "print('hi')"


def test_get_coder_unknown_raises():
    """get_coder raises KeyError for unknown name."""
    from snodo.coders import get_coder
    with pytest.raises(KeyError, match="Unknown coder 'nonexistent'"):
        get_coder("nonexistent")


def test_get_coder_error_lists_available():
    """get_coder error message lists available coders."""
    from snodo.coders import get_coder
    with pytest.raises(KeyError, match="litellm"):
        get_coder("nonexistent")


# ========== LITELLM ADAPTER TESTS ==========

def test_litellm_adapter_is_coder():
    """LiteLLMAdapter implements Coder interface."""
    from snodo.coders import LiteLLMAdapter
    assert issubclass(LiteLLMAdapter, Coder)


def test_litellm_adapter_defaults():
    """LiteLLMAdapter has correct defaults."""
    from snodo.coders import LiteLLMAdapter
    adapter = LiteLLMAdapter()
    assert adapter.model == "gpt-4"
    assert adapter.mcp_servers == []
    assert adapter.temperature == 0.7
    assert adapter.max_tokens == 4000


def test_litellm_adapter_custom_init():
    """LiteLLMAdapter accepts custom parameters."""
    from snodo.coders import LiteLLMAdapter
    server = Mock(spec=MCPServer)
    adapter = LiteLLMAdapter(
        model="claude-3-sonnet",
        mcp_servers=[server],
        temperature=0.5,
        max_tokens=2000
    )
    assert adapter.model == "claude-3-sonnet"
    assert adapter.mcp_servers == [server]
    assert adapter.temperature == 0.5
    assert adapter.max_tokens == 2000


# ========== MOCK ADAPTER TESTS ==========

def test_mock_adapter_is_coder():
    """MockAdapter implements Coder interface."""
    from snodo.coders import MockAdapter
    assert issubclass(MockAdapter, Coder)


def test_mock_adapter_defaults():
    """MockAdapter returns default files."""
    from snodo.coders import MockAdapter
    adapter = MockAdapter()
    spec = TaskSpec(description="test", constraints=[])
    result = adapter.implement(spec)
    assert len(result.files) == 2
    assert "def hello()" in result.files[0].content
    assert "def test_hello()" in result.files[1].content


def test_mock_adapter_tracks_calls():
    """MockAdapter tracks call count and last spec."""
    from snodo.coders import MockAdapter
    adapter = MockAdapter()
    spec1 = TaskSpec(description="first", constraints=[])
    spec2 = TaskSpec(description="second", constraints=[])
    adapter.implement(spec1)
    adapter.implement(spec2)
    assert adapter.call_count == 2
    assert adapter.last_spec == spec2


# ========== BACKWARD COMPATIBILITY TESTS ==========

def test_backward_compat_basic_coder_adapter():
    """BasicCoderAdapter alias works from snodo.coders."""
    from snodo.coders import BasicCoderAdapter, LiteLLMAdapter
    assert BasicCoderAdapter is LiteLLMAdapter


def test_backward_compat_mock_coder_adapter():
    """MockCoderAdapter alias works from snodo.coders."""
    from snodo.coders import MockCoderAdapter, MockAdapter
    assert MockCoderAdapter is MockAdapter


def test_backward_compat_agents_adapter_import():
    """Old import path snodo.agents.adapter still works."""
    from snodo.agents.adapter import (
        BasicCoderAdapter, MockCoderAdapter, create_coder,
        AdapterError, LLMCallError, ParseError
    )
    assert BasicCoderAdapter is not None
    assert MockCoderAdapter is not None
    assert create_coder is not None
    assert issubclass(LLMCallError, AdapterError)
    assert issubclass(ParseError, AdapterError)


def test_backward_compat_agents_adapter_new_names():
    """New names available from snodo.agents.adapter."""
    from snodo.agents.adapter import LiteLLMAdapter, MockAdapter
    assert LiteLLMAdapter is not None
    assert MockAdapter is not None


def test_backward_compat_create_coder():
    """create_coder factory works from both paths."""
    from snodo.coders import create_coder as new_create
    from snodo.agents.adapter import create_coder as old_create
    assert new_create is old_create


# ========== MODE MODEL CODER FIELDS ==========

def test_mode_coder_field_default_none():
    """Mode.coder defaults to None."""
    from snodo.compiler.models import Mode
    mode = Mode(mode_id="test", name="Test")
    assert mode.coder is None


def test_mode_coder_config_default_empty():
    """Mode.coder_config defaults to empty dict."""
    from snodo.compiler.models import Mode
    mode = Mode(mode_id="test", name="Test")
    assert mode.coder_config == {}


def test_mode_with_coder_specified():
    """Mode accepts coder and coder_config."""
    from snodo.compiler.models import Mode
    mode = Mode(
        mode_id="producer",
        name="Producer",
        coder="litellm",
        coder_config={"model": "claude-sonnet-4-20250514"}
    )
    assert mode.coder == "litellm"
    assert mode.coder_config["model"] == "claude-sonnet-4-20250514"


def test_mode_coder_config_immutable():
    """Mode is frozen (immutable)."""
    from snodo.compiler.models import Mode
    mode = Mode(
        mode_id="test",
        name="Test",
        coder="mock",
        coder_config={"key": "value"}
    )
    with pytest.raises(Exception):
        mode.coder = "other"


# ========== CREATE CODER FACTORY ==========

def test_create_coder_returns_litellm():
    """create_coder returns LiteLLMAdapter by default."""
    from snodo.coders import create_coder, LiteLLMAdapter
    coder = create_coder()
    assert isinstance(coder, LiteLLMAdapter)


def test_create_coder_returns_mock():
    """create_coder returns MockAdapter when mock=True."""
    from snodo.coders import create_coder, MockAdapter
    coder = create_coder(mock=True)
    assert isinstance(coder, MockAdapter)


def test_create_coder_custom_model():
    """create_coder passes model to LiteLLMAdapter."""
    from snodo.coders import create_coder
    coder = create_coder(model="claude-3-opus")
    assert coder.model == "claude-3-opus"


# ========== TASK 6.7: TASKSPEC CONTEXT FIELDS ==========

def test_taskspec_memory_summary_default():
    """TaskSpec.memory_summary defaults to empty string."""
    spec = TaskSpec(description="test", constraints=[])
    assert spec.memory_summary == ""


def test_taskspec_project_context_default():
    """TaskSpec.project_context defaults to empty dict."""
    spec = TaskSpec(description="test", constraints=[])
    assert spec.project_context == {}


def test_taskspec_with_context():
    """TaskSpec accepts memory_summary and project_context."""
    spec = TaskSpec(
        description="test",
        constraints=["c1"],
        memory_summary="prior work",
        project_context={"language": "python"},
    )
    assert spec.memory_summary == "prior work"
    assert spec.project_context == {"language": "python"}
