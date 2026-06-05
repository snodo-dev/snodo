"""Tests for agent adapter layer.

FILE: tests/agents/test_adapter.py

Tests cover:
- BasicCoderAdapter with mocked LLM (fast, deterministic)
- MockCoderAdapter for testing
- Prompt building
- Response parsing
- Error handling
- MCP server integration
- Integration test with simple spec
- 100% coverage
"""

import pytest
from unittest.mock import Mock

from snodo.core.interfaces import TaskSpec, CodeArtifact, FileArtifact, MCPServer
from snodo.agents.adapter import (
    BasicCoderAdapter, MockCoderAdapter, create_coder,
    AdapterError, LLMCallError, ParseError
)


# ========== FIXTURES ==========

@pytest.fixture
def simple_spec():
    """Create a simple task specification."""
    return TaskSpec(
        description="Create a function that adds two numbers",
        constraints=["Must handle negative numbers", "Include type hints"]
    )


@pytest.fixture
def mock_mcp_server():
    """Create a mock MCP server."""
    server = Mock(spec=MCPServer)
    server.execute_tool = Mock(return_value="tool_result")
    return server


# ========== MOCK CODER ADAPTER TESTS ==========

def test_mock_adapter_returns_default_code():
    """Test MockCoderAdapter returns default files."""
    adapter = MockCoderAdapter()
    spec = TaskSpec(description="test", constraints=[])

    result = adapter.implement(spec)

    assert isinstance(result, CodeArtifact)
    assert len(result.files) == 2
    assert "def hello()" in result.files[0].content
    assert "def test_hello()" in result.files[1].content


def test_mock_adapter_returns_custom_code():
    """Test MockCoderAdapter with custom files."""
    custom_files = [
        FileArtifact(path="src/foo.py", content="def foo():\n    pass"),
        FileArtifact(path="tests/test_foo.py", content="def test_foo():\n    pass"),
    ]

    adapter = MockCoderAdapter(
        mock_files=custom_files
    )
    spec = TaskSpec(description="test", constraints=[])

    result = adapter.implement(spec)

    assert result.files[0].content == "def foo():\n    pass"
    assert result.files[1].content == "def test_foo():\n    pass"


def test_mock_adapter_tracks_calls():
    """Test MockCoderAdapter tracks call count and last spec."""
    adapter = MockCoderAdapter()
    
    spec1 = TaskSpec(description="first", constraints=[])
    spec2 = TaskSpec(description="second", constraints=[])
    
    adapter.implement(spec1)
    adapter.implement(spec2)
    
    assert adapter.call_count == 2
    assert adapter.last_spec == spec2


# ========== BASIC CODER ADAPTER INITIALIZATION TESTS ==========

def test_basic_adapter_init_defaults():
    """Test BasicCoderAdapter initialization with defaults."""
    adapter = BasicCoderAdapter()
    
    assert adapter.model == "claude-sonnet-4-20250514"
    assert adapter.mcp_servers == []
    assert adapter.temperature == 0.7
    assert adapter.max_tokens == 16000


def test_basic_adapter_init_custom():
    """Test BasicCoderAdapter with custom parameters."""
    servers = [Mock(spec=MCPServer)]
    
    adapter = BasicCoderAdapter(
        model="claude-3-sonnet",
        mcp_servers=servers,
        temperature=0.5,
        max_tokens=2000
    )
    
    assert adapter.model == "claude-3-sonnet"
    assert adapter.mcp_servers == servers
    assert adapter.temperature == 0.5
    assert adapter.max_tokens == 2000


# ========== PROMPT BUILDING TESTS ==========

def test_build_prompt_basic(simple_spec):
    """Test building prompt from simple spec."""
    adapter = BasicCoderAdapter()

    prompt = adapter._build_prompt(simple_spec)

    assert "Create a function that adds two numbers" in prompt
    assert "Must handle negative numbers" in prompt
    assert "Include type hints" in prompt
    assert "```json" in prompt


def test_build_prompt_no_constraints():
    """Test building prompt without constraints."""
    spec = TaskSpec(description="Simple task", constraints=[])
    adapter = BasicCoderAdapter()

    prompt = adapter._build_prompt(spec)

    assert "Simple task" in prompt
    assert "Constraints:" not in prompt


def test_build_prompt_format():
    """Test prompt includes required format instructions."""
    spec = TaskSpec(description="test", constraints=[])
    adapter = BasicCoderAdapter()

    prompt = adapter._build_prompt(spec)

    assert "```json" in prompt
    assert "path" in prompt
    assert "content" in prompt


# ========== RESPONSE PARSING TESTS ==========

def test_parse_response_valid():
    """Test parsing valid LLM response with JSON."""
    adapter = BasicCoderAdapter()

    response = """Here's the implementation:

```json
[
  {"path": "src/add.py", "content": "def add(a: int, b: int) -> int:\\n    return a + b\\n"},
  {"path": "tests/test_add.py", "content": "def test_add():\\n    assert add(2, 3) == 5\\n"}
]
```
"""

    result = adapter._parse_response(response)

    assert isinstance(result, CodeArtifact)
    assert len(result.files) == 2
    assert "def add" in result.files[0].content
    assert "def test_add" in result.files[1].content


def test_parse_response_direct_json():
    """Test parsing response that is direct JSON (no code block)."""
    adapter = BasicCoderAdapter()

    import json
    response = json.dumps([
        {"path": "src/foo.py", "content": "def foo():\n    pass\n"},
        {"path": "tests/test_foo.py", "content": "def test_foo():\n    pass\n"},
    ])

    result = adapter._parse_response(response)

    assert len(result.files) == 2
    assert "def foo" in result.files[0].content
    assert "def test_foo" in result.files[1].content


def test_parse_response_missing_path_raises():
    """Test parsing response with missing path key."""
    adapter = BasicCoderAdapter()

    import json
    response = json.dumps([{"content": "x"}])

    with pytest.raises(ParseError, match="path"):
        adapter._parse_response(response)


def test_parse_response_not_json_raises():
    """Test parsing response that is not JSON."""
    adapter = BasicCoderAdapter()

    response = "Just some text without any JSON"

    with pytest.raises(ParseError, match="Failed to parse"):
        adapter._parse_response(response)


def test_parse_response_default_action():
    """Test parsing response defaults action to write."""
    adapter = BasicCoderAdapter()

    import json
    response = json.dumps([
        {"path": "src/foo.py", "content": "code"},
    ])

    result = adapter._parse_response(response)
    assert result.files[0].action == "write"


# ========== LLM CALL TESTS (MOCKED) ==========

def test_call_llm_success():
    """Test successful LLM call."""
    adapter = BasicCoderAdapter()
    
    # Mock the completion response
    mock_response = Mock()
    mock_response.choices = [Mock()]
    mock_response.choices[0].message.content = "response text"
    
    # Set the mock directly on the instance
    adapter._completion_fn = Mock(return_value=mock_response)
    
    result = adapter._call_llm("test prompt")
    
    assert result == "response text"
    adapter._completion_fn.assert_called_once()


def test_call_llm_no_litellm_raises():
    """Test LLM call without litellm installed."""
    adapter = BasicCoderAdapter()
    adapter._completion_fn = None
    
    with pytest.raises(LLMCallError, match="litellm not available"):
        adapter._call_llm("test prompt")


def test_call_llm_api_error_raises():
    """Test LLM call with API error."""
    adapter = BasicCoderAdapter()
    
    # Set the mock to raise an exception
    adapter._completion_fn = Mock(side_effect=Exception("API error"))
    
    with pytest.raises(LLMCallError, match="LLM call failed"):
        adapter._call_llm("test prompt")


# ========== MCP SERVER INTEGRATION TESTS ==========

def test_attach_mcp_tool(mock_mcp_server):
    """Test attaching MCP server."""
    adapter = BasicCoderAdapter()
    
    adapter.attach_mcp_tool(mock_mcp_server)
    
    assert mock_mcp_server in adapter.mcp_servers


def test_attach_mcp_tool_no_duplicates(mock_mcp_server):
    """Test attaching same MCP server twice doesn't duplicate."""
    adapter = BasicCoderAdapter()
    
    adapter.attach_mcp_tool(mock_mcp_server)
    adapter.attach_mcp_tool(mock_mcp_server)
    
    assert len(adapter.mcp_servers) == 1


def test_list_available_tools():
    """Test listing available MCP tools."""
    server1 = Mock(spec=MCPServer)
    server2 = Mock(spec=MCPServer)
    
    adapter = BasicCoderAdapter(mcp_servers=[server1, server2])
    
    tools = adapter.list_available_tools()
    
    assert len(tools) == 2
    assert "mcp_server_0" in tools
    assert "mcp_server_1" in tools


# ========== FACTORY FUNCTION TESTS ==========

def test_create_coder_basic():
    """Test create_coder returns BasicCoderAdapter."""
    coder = create_coder()
    
    assert isinstance(coder, BasicCoderAdapter)
    assert coder.model == "claude-sonnet-4-20250514"


def test_create_coder_custom_model():
    """Test create_coder with custom model."""
    coder = create_coder(model="claude-3-opus")
    
    assert isinstance(coder, BasicCoderAdapter)
    assert coder.model == "claude-3-opus"


def test_create_coder_mock():
    """Test create_coder returns MockCoderAdapter when mock=True."""
    coder = create_coder(mock=True)
    
    assert isinstance(coder, MockCoderAdapter)


def test_create_coder_with_mcp_servers(mock_mcp_server):
    """Test create_coder with MCP servers."""
    coder = create_coder(mcp_servers=[mock_mcp_server])
    
    assert isinstance(coder, BasicCoderAdapter)
    assert mock_mcp_server in coder.mcp_servers


# ========== INTEGRATION TEST ==========

def test_integration_simple_spec():
    """Integration test: give adapter a spec, verify output structure."""
    # Use mock adapter for deterministic testing
    adapter = MockCoderAdapter(
        mock_files=[
            FileArtifact(path="src/add.py", content="def add(a, b):\n    return a + b"),
            FileArtifact(path="tests/test_add.py", content="def test_add():\n    assert add(1, 2) == 3"),
        ]
    )

    spec = TaskSpec(
        description="Create an add function",
        constraints=["Must accept two arguments"]
    )

    result = adapter.implement(spec)

    # Verify structure
    assert isinstance(result, CodeArtifact)
    assert len(result.files) == 2

    # Verify content makes sense
    assert "def add" in result.files[0].content
    assert "def test_add" in result.files[1].content
    assert "return a + b" in result.files[0].content


def test_integration_basic_adapter_mocked():
    """Integration test: BasicCoderAdapter with mocked LLM."""
    import json as _json

    adapter = BasicCoderAdapter()

    # Mock LLM response — JSON array of file operations
    json_payload = _json.dumps([
        {"path": "src/multiply.py", "content": "def multiply(a: int, b: int) -> int:\n    return a * b\n"},
        {"path": "tests/test_multiply.py", "content": "def test_multiply():\n    assert multiply(2, 3) == 6\n"},
    ])

    mock_response = Mock()
    mock_response.choices = [Mock()]
    mock_response.choices[0].message.content = f"```json\n{json_payload}\n```"

    # Set the mock directly on the instance
    adapter._completion_fn = Mock(return_value=mock_response)

    spec = TaskSpec(
        description="Create multiply function",
        constraints=["Include type hints"]
    )

    result = adapter.implement(spec)

    assert isinstance(result, CodeArtifact)
    assert len(result.files) == 2
    assert "def multiply" in result.files[0].content
    assert "def test_multiply" in result.files[1].content
    assert adapter._completion_fn.called


# ========== ERROR HANDLING EDGE CASES ==========

def test_exception_classes_exist():
    """Test that all exception classes are defined."""
    assert issubclass(AdapterError, Exception)
    assert issubclass(LLMCallError, AdapterError)
    assert issubclass(ParseError, AdapterError)


def test_parse_response_no_json():
    """Test parsing response with no JSON."""
    adapter = BasicCoderAdapter()

    response = "Just some text without any JSON"

    with pytest.raises(ParseError, match="Failed to parse"):
        adapter._parse_response(response)


def test_parse_response_malformed_json():
    """Test parsing response with malformed JSON."""
    adapter = BasicCoderAdapter()

    response = """
```json
[{"path": "src/foo.py", INVALID JSON
```
"""

    with pytest.raises(ParseError):
        adapter._parse_response(response)


# ========== COMPREHENSIVE COVERAGE TESTS ==========

def test_adapter_with_empty_mcp_servers_list():
    """Test adapter with explicitly empty MCP servers."""
    adapter = BasicCoderAdapter(mcp_servers=[])
    
    assert adapter.mcp_servers == []
    assert adapter.list_available_tools() == []


def test_build_prompt_with_many_constraints():
    """Test prompt building with many constraints."""
    spec = TaskSpec(
        description="Complex task",
        constraints=[
            "Constraint 1",
            "Constraint 2",
            "Constraint 3",
            "Constraint 4"
        ]
    )

    adapter = BasicCoderAdapter()
    prompt = adapter._build_prompt(spec)

    for constraint in spec.constraints:
        assert constraint in prompt
    assert "```json" in prompt


# ========== TASK 6.7: CONTEXT MANAGEMENT TESTS ==========

def test_build_prompt_includes_project_context():
    """Prompt includes project context when provided."""
    spec = TaskSpec(
        description="Add feature",
        constraints=[],
        project_context={
            "language": "python",
            "structure": "src/\n  main.py\ntests/",
            "config_files": {"pyproject.toml": "[build-system]"},
        },
    )
    adapter = BasicCoderAdapter()
    prompt = adapter._build_prompt(spec)
    assert "python project" in prompt
    assert "src/" in prompt
    assert "pyproject.toml" in prompt
    assert "[build-system]" in prompt


def test_build_prompt_includes_memory_summary():
    """Prompt includes memory summary when provided."""
    spec = TaskSpec(
        description="Continue work",
        constraints=[],
        memory_summary="Previously implemented auth module in src/auth.py.",
    )
    adapter = BasicCoderAdapter()
    prompt = adapter._build_prompt(spec)
    assert "Session History" in prompt
    assert "auth module" in prompt


def test_build_prompt_no_context_no_memory():
    """Prompt works with empty context and memory (backward compatible)."""
    spec = TaskSpec(description="Simple task", constraints=[])
    adapter = BasicCoderAdapter()
    prompt = adapter._build_prompt(spec)
    assert "Simple task" in prompt
    assert "Project Context" not in prompt
    assert "Session History" not in prompt


def test_build_prompt_unknown_language_no_hint():
    """Prompt does not include language hint when unknown."""
    spec = TaskSpec(
        description="Task",
        constraints=[],
        project_context={"language": "unknown"},
    )
    adapter = BasicCoderAdapter()
    prompt = adapter._build_prompt(spec)
    assert "unknown project" not in prompt


def test_taskspec_default_fields():
    """TaskSpec new fields have correct defaults."""
    spec = TaskSpec(description="test", constraints=["c1"])
    assert spec.memory_summary == ""
    assert spec.project_context == {}


def test_mock_adapter_ignores_context():
    """MockAdapter works with enriched TaskSpec (ignores extra fields)."""
    spec = TaskSpec(
        description="test",
        constraints=[],
        memory_summary="prior context",
        project_context={"language": "python"},
    )
    adapter = MockCoderAdapter()
    result = adapter.implement(spec)
    assert len(result.files) == 2
    assert adapter.last_spec.memory_summary == "prior context"
