"""Tests for snodo.coders module.

FILE: tests/coders/test_coders.py

Tests cover:
- Registry pattern (get_coder, CODER_REGISTRY)
- CoderAdapter base class
- LiteLLMAdapter (renamed from BasicCoderAdapter)
- MockAdapter (renamed from MockCoderAdapter)
- Backward-compatible aliases
- Mode model coder fields
- Coder registry pattern (get_coder, CODER_REGISTRY)
"""

import json
from unittest.mock import Mock

import pytest
from snodo.coders.base import LLMCallError, ParseError
from snodo.core.interfaces import Coder, FileArtifact, MCPServer, TaskSpec

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
    from snodo.coders import LiteLLMAdapter, get_coder
    coder = get_coder("litellm", model="gpt-4")
    assert isinstance(coder, LiteLLMAdapter)
    assert coder.model == "gpt-4"


def test_get_coder_mock():
    """get_coder returns MockAdapter for 'mock'."""
    from snodo.coders import MockAdapter, get_coder
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
    assert adapter.model == "claude-sonnet-4-20250514"
    assert adapter.mcp_servers == []
    assert adapter.temperature == 0.7
    assert adapter.max_tokens == 16000


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
    from snodo.coders import MockAdapter, MockCoderAdapter
    assert MockCoderAdapter is MockAdapter


def test_backward_compat_agents_adapter_import():
    """Old import path snodo.agents.adapter still works."""
    from snodo.agents.adapter import (
        AdapterError,
        BasicCoderAdapter,
        LLMCallError,
        MockCoderAdapter,
        ParseError,
    )
    assert BasicCoderAdapter is not None
    assert MockCoderAdapter is not None
    assert issubclass(LLMCallError, AdapterError)
    assert issubclass(ParseError, AdapterError)


def test_backward_compat_agents_adapter_new_names():
    """New names available from snodo.agents.adapter."""
    from snodo.agents.adapter import LiteLLMAdapter, MockAdapter
    assert LiteLLMAdapter is not None
    assert MockAdapter is not None


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


# === Coder Read-Before-Write Tool Loop Tests ===

class TestCoderToolLoop:
    """Tests for the bounded read-only tool-use loop in LiteLLMAdapter."""

    def _make_code_artifact_response(self, files=None):
        """Create a mock LLM response with submit_files tool call."""
        if files is None:
            files = [{"path": "src/main.py", "content": "def main(): pass", "action": "write"}]
        response = Mock()
        response.choices = [Mock()]
        response.choices[0].message.content = None
        tc = Mock()
        tc.id = "tc_submit"
        tc.function.name = "submit_files"
        tc.function.arguments = json.dumps({"files": files})
        response.choices[0].message.tool_calls = [tc]
        return response

    def _make_done_response(self, content="Done"):
        """Create a mock LLM response with no tool calls (turn complete)."""
        response = Mock()
        response.choices = [Mock()]
        response.choices[0].message.content = content
        response.choices[0].message.tool_calls = None
        return response

    def _make_legacy_response(self, files=None):
        """Create a mock LLM response with free-text JSON (single-completion path)."""
        if files is None:
            files = [{"path": "src/main.py", "content": "def main(): pass", "action": "write"}]
        response = Mock()
        response.choices = [Mock()]
        response.choices[0].message.content = json.dumps(files)
        response.choices[0].message.tool_calls = None
        return response

    def _make_tool_call_response(self, tool_name, arguments, content=None):
        """Create a mock LLM response with a tool call."""
        response = Mock()
        response.choices = [Mock()]
        tool_call = Mock()
        tool_call.id = "tc_1"
        tool_call.function.name = tool_name
        tool_call.function.arguments = json.dumps(arguments)
        response.choices[0].message.content = content
        response.choices[0].message.tool_calls = [tool_call]
        return response

    def test_tool_loop_reads_file_before_code_artifact(self):
        """Coder should call read_file before producing CodeArtifact."""
        from snodo.coders import LiteLLMAdapter

        workspace = Mock()
        workspace.read_file.return_value = "def old_function():\n    return 1\n"

        call_count = [0]

        def completion_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return self._make_tool_call_response(
                    "read_file", {"path": "src/main.py"}
                )
            elif call_count[0] == 2:
                return self._make_code_artifact_response([
                    {"path": "src/main.py", "content": "def old_function():\n    return 1\n\ndef new_function():\n    return 2\n", "action": "write"}
                ])
            return self._make_done_response()

        completion_fn = Mock(side_effect=completion_side_effect)
        coder = LiteLLMAdapter(model="gpt-4", workspace_mcp=workspace)
        coder._completion_fn = completion_fn

        spec = TaskSpec(description="Add new_function to src/main.py", constraints=[])
        result = coder.implement(spec)

        assert len(result.files) == 1
        assert result.files[0].path == "src/main.py"
        assert "new_function" in result.files[0].content
        workspace.read_file.assert_called_once_with("src/main.py")
        assert completion_fn.call_count == 3

    def test_tool_loop_reads_file_lines(self):
        """Coder should call read_file_lines for partial reads."""
        from snodo.coders import LiteLLMAdapter

        workspace = Mock()
        workspace.read_file_lines.return_value = "class MyClass:\n    def method(self): pass"

        call_count = [0]

        def completion_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return self._make_tool_call_response(
                    "read_file_lines", {"path": "src/models.py", "start": 1, "end": 20}
                )
            elif call_count[0] == 2:
                return self._make_code_artifact_response()
            return self._make_done_response()

        completion_fn = Mock(side_effect=completion_side_effect)
        coder = LiteLLMAdapter(model="gpt-4", workspace_mcp=workspace)
        coder._completion_fn = completion_fn

        spec = TaskSpec(description="Modify MyClass", constraints=[])
        result = coder.implement(spec)

        assert len(result.files) == 1
        workspace.read_file_lines.assert_called_once_with("src/models.py", 1, 20)

    def test_tool_loop_lists_files(self):
        """Coder should call list_files to explore project."""
        from snodo.coders import LiteLLMAdapter

        workspace = Mock()
        workspace.list_files.return_value = ["main.py", "utils.py", "models.py"]

        call_count = [0]

        def completion_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return self._make_tool_call_response(
                    "list_files", {"directory": "src"}
                )
            elif call_count[0] == 2:
                return self._make_code_artifact_response()
            return self._make_done_response()

        completion_fn = Mock(side_effect=completion_side_effect)
        coder = LiteLLMAdapter(model="gpt-4", workspace_mcp=workspace)
        coder._completion_fn = completion_fn

        spec = TaskSpec(description="Create new module", constraints=[])
        result = coder.implement(spec)

        assert len(result.files) == 1
        workspace.list_files.assert_called_once_with("src")

    def test_tool_loop_bounded_at_max_turns(self):
        """Coder loop bounded at max turns."""
        from snodo.coders import LiteLLMAdapter

        workspace = Mock()

        # Always return a tool call, never CodeArtifact
        def completion_side_effect(**kwargs):
            return self._make_tool_call_response(
                "read_file", {"path": "x.py"}
            )

        completion_fn = Mock(side_effect=completion_side_effect)
        coder = LiteLLMAdapter(model="gpt-4", workspace_mcp=workspace)
        coder._completion_fn = completion_fn

        spec = TaskSpec(description="Infinite loop", constraints=[])
        with pytest.raises(ParseError, match="completed the tool loop without delivering files"):
            coder.implement(spec)

    def test_no_read_returns_code_artifact_first_turn(self):
        """When no read is needed, returns CodeArtifact across turns."""
        from snodo.coders import LiteLLMAdapter

        workspace = Mock()
        completion_fn = Mock(side_effect=[
            self._make_code_artifact_response(),
            self._make_done_response(),
        ])

        coder = LiteLLMAdapter(model="gpt-4", workspace_mcp=workspace)
        coder._completion_fn = completion_fn

        spec = TaskSpec(description="Create new file", constraints=[])
        result = coder.implement(spec)

        assert len(result.files) == 1
        assert result.files[0].path == "src/main.py"
        assert completion_fn.call_count == 2

    def test_no_workspace_falls_back_to_single_completion(self):
        """Without workspace_mcp, uses single-completion path."""
        from snodo.coders import LiteLLMAdapter

        completion_fn = Mock(return_value=self._make_legacy_response())

        coder = LiteLLMAdapter(model="gpt-4")
        coder._completion_fn = completion_fn

        spec = TaskSpec(description="Create new file", constraints=[])
        result = coder.implement(spec)

        assert len(result.files) == 1
        assert completion_fn.call_count == 1
        # Verify no tools kwarg was passed
        call_kwargs = completion_fn.call_args[1]
        assert "tools" not in call_kwargs

    def test_tool_loop_handles_tool_error_gracefully(self):
        """If a tool call fails, error is fed back and loop continues."""
        from snodo.coders import LiteLLMAdapter

        workspace = Mock()
        workspace.read_file.side_effect = FileNotFoundError("not found")

        call_count = [0]

        def completion_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return self._make_tool_call_response(
                    "read_file", {"path": "missing.py"}
                )
            elif call_count[0] == 2:
                return self._make_code_artifact_response()
            return self._make_done_response()

        completion_fn = Mock(side_effect=completion_side_effect)
        coder = LiteLLMAdapter(model="gpt-4", workspace_mcp=workspace)
        coder._completion_fn = completion_fn

        spec = TaskSpec(description="Modify missing.py", constraints=[])
        result = coder.implement(spec)

        assert len(result.files) == 1
        assert completion_fn.call_count == 3

    def test_tool_loop_llm_exception_raises_llm_call_error(self):
        """If LLM call throws, raises LLMCallError."""
        from snodo.coders import LiteLLMAdapter

        workspace = Mock()
        completion_fn = Mock(side_effect=Exception("API down"))

        coder = LiteLLMAdapter(model="gpt-4", workspace_mcp=workspace)
        coder._completion_fn = completion_fn

        spec = TaskSpec(description="Test", constraints=[])

        with pytest.raises(LLMCallError, match="tool-loop error"):
            coder.implement(spec)

    def test_tool_loop_llm_exception_with_staged_files_raises_llm_call_error(self):
        """If LLM call throws on turn 2 after staging files on turn 1, raises LLMCallError with staged info."""
        from snodo.coders import LiteLLMAdapter

        workspace = Mock()
        call_count = [0]

        def completion_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return self._make_code_artifact_response([
                    {"path": "src/file1.py", "content": "print('1')", "action": "write"}
                ])
            raise Exception("Connection timed out after 600s")

        completion_fn = Mock(side_effect=completion_side_effect)
        coder = LiteLLMAdapter(model="gpt-4", workspace_mcp=workspace)
        coder._completion_fn = completion_fn

        spec = TaskSpec(description="Test staged timeout", constraints=[])

        with pytest.raises(LLMCallError, match="1 file\(s\) staged"):
            coder.implement(spec)

    def test_tool_loop_uses_tools_kwarg(self):
        """Tool loop must pass tools=[...] to completion_fn."""
        from snodo.coders import LiteLLMAdapter

        workspace = Mock()
        completion_fn = Mock(return_value=self._make_code_artifact_response())

        coder = LiteLLMAdapter(model="gpt-4", workspace_mcp=workspace)
        coder._completion_fn = completion_fn

        spec = TaskSpec(description="Test", constraints=[])
        coder.implement(spec)

        call_kwargs = completion_fn.call_args[1]
        assert "tools" in call_kwargs
        assert isinstance(call_kwargs["tools"], list)
        assert call_kwargs.get("parallel_tool_calls") is True
        tool_names = [t["function"]["name"] for t in call_kwargs["tools"]]
        assert "read_files" in tool_names
        assert "read_file" in tool_names
        assert "read_file_lines" in tool_names
        assert "list_files" in tool_names

    def test_submit_files_accumulates_across_turns(self):
        """submit_files calls accumulate files across turns until finished."""
        from snodo.coders import LiteLLMAdapter

        workspace = Mock()
        call_count = [0]

        def completion_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return self._make_code_artifact_response([
                    {"path": "src/file1.py", "content": "print('1')", "action": "write"}
                ])
            elif call_count[0] == 2:
                return self._make_code_artifact_response([
                    {"path": "src/file2.py", "content": "print('2')", "action": "write"}
                ])
            # Turn 3: model returns assistant message with no tool calls (done)
            response = Mock()
            response.choices = [Mock()]
            response.choices[0].message.content = "All files submitted."
            response.choices[0].message.tool_calls = None
            return response

        completion_fn = Mock(side_effect=completion_side_effect)
        coder = LiteLLMAdapter(model="gpt-4", workspace_mcp=workspace)
        coder._completion_fn = completion_fn

        spec = TaskSpec(description="Multi-file change", constraints=[])
        result = coder.implement(spec)

        assert len(result.files) == 2
        paths = [f.path for f in result.files]
        assert "src/file1.py" in paths
        assert "src/file2.py" in paths

    def test_tool_loop_batch_read_files(self):
        """read_files batch reads multiple files in a single tool call."""
        from snodo.coders import LiteLLMAdapter

        workspace = Mock()
        workspace.read_file.side_effect = lambda p: f"content of {p}"
        call_count = [0]

        def completion_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return self._make_tool_call_response(
                    "read_files", {"paths": ["src/a.py", "src/b.py"]}
                )
            return self._make_code_artifact_response([
                {"path": "src/a.py", "content": "updated a", "action": "write"}
            ])

        completion_fn = Mock(side_effect=completion_side_effect)
        coder = LiteLLMAdapter(model="gpt-4", workspace_mcp=workspace)
        coder._completion_fn = completion_fn

        spec = TaskSpec(description="Read multiple files", constraints=[])
        result = coder.implement(spec)

        assert workspace.read_file.call_count == 2
        workspace.read_file.assert_any_call("src/a.py")
        workspace.read_file.assert_any_call("src/b.py")
        assert len(result.files) == 1

    def test_read_files_path_cap_truncation(self):
        """read_files caps requests to max 10 paths and informs the coder of omitted paths."""
        from snodo.coders.litellm import LiteLLMAdapter

        workspace = Mock()
        workspace.read_file.side_effect = lambda p: f"content of {p}"

        paths = [f"src/file_{i}.py" for i in range(15)]
        output = LiteLLMAdapter._execute_tool("read_files", {"paths": paths}, workspace)

        assert workspace.read_file.call_count == 10
        assert "[TRUNCATED BATCH: 5 path(s) omitted" in output
        assert "src/file_10.py" in output
        assert "src/file_14.py" in output

    def test_read_files_byte_cap_truncation(self):
        """read_files caps output bytes to 32KB limit and informs coder of truncation."""
        from snodo.coders.litellm import LiteLLMAdapter

        workspace = Mock()
        # Each file is ~10KB
        workspace.read_file.side_effect = lambda p: "x" * 10240

        paths = ["src/a.py", "src/b.py", "src/c.py", "src/d.py"]
        output = LiteLLMAdapter._execute_tool("read_files", {"paths": paths}, workspace)

        assert "[TRUNCATED BATCH:" in output
        assert "content truncated at 32KB batch limit" in output

    def test_read_files_duplicate_paths_truncation(self):
        """read_files handles duplicate paths during byte-cap truncation without index corruption."""
        from snodo.coders.litellm import LiteLLMAdapter

        workspace = Mock()
        workspace.read_file.side_effect = lambda p: "x" * 20000

        paths = ["src/a.py", "src/a.py", "src/b.py"]
        output = LiteLLMAdapter._execute_tool("read_files", {"paths": paths}, workspace)

        assert "[TRUNCATED BATCH:" in output
        assert "1 path(s) omitted (src/b.py)" in output

    def test_prompt_mentions_tools_when_workspace_available(self):
        """Prompt should mention available tools when workspace_mcp is set."""
        from snodo.coders import LiteLLMAdapter

        workspace = Mock()
        coder = LiteLLMAdapter(model="gpt-4", workspace_mcp=workspace)

        spec = TaskSpec(description="Test", constraints=[])
        prompt = coder._build_prompt(spec)

        assert "read_files" in prompt
        assert "read_file" in prompt
        assert "read_file_lines" in prompt
        assert "list_files" in prompt
        assert "turns are expensive" in prompt.lower()

    def test_prompt_no_tool_mention_without_workspace(self):
        """Prompt should NOT mention tools when workspace_mcp is None."""
        from snodo.coders import LiteLLMAdapter

        coder = LiteLLMAdapter(model="gpt-4")

        spec = TaskSpec(description="Test", constraints=[])
        prompt = coder._build_prompt(spec)

        assert "read_file" not in prompt
        assert "Available Tools" not in prompt

    def test_workspace_mcp_in_init(self):
        """LiteLLMAdapter accepts workspace_mcp in __init__."""
        from snodo.coders import LiteLLMAdapter

        workspace = Mock()
        coder = LiteLLMAdapter(model="gpt-4", workspace_mcp=workspace)

        assert coder.workspace_mcp is workspace

    def test_workspace_mcp_default_none(self):
        """workspace_mcp defaults to None."""
        from snodo.coders import LiteLLMAdapter

        coder = LiteLLMAdapter()
        assert coder.workspace_mcp is None

    def test_parse_code_artifact_delete_without_content(self):
        """Parsing submit_files JSON with action 'delete' and no content key succeeds."""
        from snodo.coders import LiteLLMAdapter

        coder = LiteLLMAdapter()
        raw_json = json.dumps([
            {"path": "src/new_file.py", "content": "print(1)", "action": "write"},
            {"path": "src/orphan_file.py", "action": "delete"},
        ])
        artifact = coder._parse_response(raw_json)

        assert len(artifact.files) == 2
        assert artifact.files[0].path == "src/new_file.py"
        assert artifact.files[0].action == "write"

        assert artifact.files[1].path == "src/orphan_file.py"
        assert artifact.files[1].action == "delete"
        assert artifact.files[1].content == ""
