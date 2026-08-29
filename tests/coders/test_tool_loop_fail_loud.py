"""Tests for tool-loop fail-loud behavior (W4-03)."""

import json
from unittest.mock import MagicMock

import pytest
from snodo.coders.base import ParseError
from snodo.coders.litellm import LiteLLMAdapter


def _assert_tool_call_ids_answered(messages):
    """Assert every tool_call_id in an assistant message is answered by a
    tool message before the next request.

    This is the invariant providers enforce: an assistant message with
    tool_calls must be followed by a tool message for each tool_call_id
    before the next request. It must hold for terminal tools, failing
    tools, tools returning nothing, and turns mixing several calls.
    """
    pending = set()
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                pending.add(tc["id"])
        elif m.get("role") == "tool":
            pending.discard(m.get("tool_call_id"))
    assert not pending, (
        f"tool_call_ids without a tool response before the next request: {sorted(pending)}"
    )


def _make_mock_response(content=None, tool_calls=None, finish_reason="stop"):
    """Create a mock litellm response object."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.choices[0].message.tool_calls = tool_calls or []
    response.choices[0].finish_reason = finish_reason
    return response


def _make_submit_files_call(files):
    """Create a mock tool call for submit_files."""
    tc = MagicMock()
    tc.function.name = "submit_files"
    tc.function.arguments = json.dumps({"files": files})
    tc.id = "call_001"
    return tc


def _make_read_file_call(path="test.py"):
    """Create a mock tool call for read_file."""
    tc = MagicMock()
    tc.function.name = "read_file"
    tc.function.arguments = json.dumps({"path": path})
    tc.id = "call_001"
    return tc


class TestToolLoopSubmitFilesHappyPath:
    """submit_files happy path is unchanged."""

    def test_submit_files_returns_json(self):
        adapter = LiteLLMAdapter(workspace_mcp=MagicMock())
        adapter._completion_fn = MagicMock(return_value=_make_mock_response(
            tool_calls=[_make_submit_files_call([
                {"path": "src/main.py", "content": "print('hi')"},
            ])],
        ))
        result = adapter._call_llm_with_tools("prompt")
        parsed = json.loads(result)
        assert len(parsed) == 1
        assert parsed[0]["path"] == "src/main.py"

    def test_submit_files_after_read_tool(self):
        workspace = MagicMock()
        workspace.read_file.return_value = "old content"
        adapter = LiteLLMAdapter(workspace_mcp=workspace)

        # First turn: read_file, second turn: submit_files
        adapter._completion_fn = MagicMock(side_effect=[
            _make_mock_response(
                content=None,
                tool_calls=[_make_read_file_call()],
                finish_reason="tool_calls",
            ),
            _make_mock_response(
                tool_calls=[_make_submit_files_call([
                    {"path": "src/main.py", "content": "new content"},
                ])],
            ),
        ])
        result = adapter._call_llm_with_tools("prompt")
        parsed = json.loads(result)
        assert len(parsed) == 1
        assert workspace.read_file.called


class TestToolLoopParseableFreeText:
    """Loop ending with parseable JSON still succeeds."""

    def test_valid_json_array_succeeds(self):
        adapter = LiteLLMAdapter(workspace_mcp=MagicMock())
        adapter._completion_fn = MagicMock(side_effect=[
            _make_mock_response(content="No tools needed"),
            _make_mock_response(
                content=json.dumps([
                    {"path": "src/main.py", "content": "print('hi')"},
                ]),
                finish_reason="stop",
            ),
        ])
        result = adapter._call_llm_with_tools("prompt")
        parsed = json.loads(result)
        assert len(parsed) == 1

    def test_valid_json_in_code_block_succeeds(self):
        adapter = LiteLLMAdapter(workspace_mcp=MagicMock())
        adapter._completion_fn = MagicMock(side_effect=[
            _make_mock_response(content="No tools needed"),
            _make_mock_response(
                content='```json\n[{"path": "src/main.py", "content": "hi"}]\n```',
                finish_reason="stop",
            ),
        ])
        # _call_llm_with_tools returns the raw content; _parse_response handles fence extraction
        raw = adapter._call_llm_with_tools("prompt")
        artifact = adapter._parse_response(raw)
        assert len(artifact.files) == 1


class TestToolLoopEmptyContent:
    """Loop ending with empty content raises diagnostic ParseError."""

    def test_empty_string_raises(self):
        adapter = LiteLLMAdapter(workspace_mcp=MagicMock())
        adapter._completion_fn = MagicMock(side_effect=[
            _make_mock_response(content="No tools"),
            _make_mock_response(content="", finish_reason="stop"),
        ])
        with pytest.raises(ParseError) as exc_info:
            adapter._call_llm_with_tools("prompt")
        err = str(exc_info.value)
        assert "submit_files" in err
        assert "LiteLLMAdapter" not in err  # no default model name leak
        assert "(empty)" in err

    def test_none_content_raises(self):
        adapter = LiteLLMAdapter(workspace_mcp=MagicMock())
        adapter._completion_fn = MagicMock(side_effect=[
            _make_mock_response(content="No tools"),
            _make_mock_response(content=None, finish_reason="stop"),
        ])
        with pytest.raises(ParseError) as exc_info:
            adapter._call_llm_with_tools("prompt")
        err = str(exc_info.value)
        assert "submit_files" in err
        # Last assistant content ("No tools") is used as preview
        assert "No tools" in err


class TestToolLoopUnparseableProse:
    """Loop ending with prose raises diagnostic ParseError with preview."""

    def test_prose_raises_with_preview(self):
        adapter = LiteLLMAdapter(workspace_mcp=MagicMock())
        prose = "I'm sorry, I cannot help with that task. " * 10
        adapter._completion_fn = MagicMock(side_effect=[
            _make_mock_response(content="No tools"),
            _make_mock_response(content=prose, finish_reason="stop"),
        ])
        with pytest.raises(ParseError) as exc_info:
            adapter._call_llm_with_tools("prompt")
        err = str(exc_info.value)
        assert "submit_files" in err
        assert "I'm sorry" in err  # content preview included

    def test_turn_cap_prose_raises(self):
        adapter = LiteLLMAdapter(workspace_mcp=MagicMock(), max_tool_turns=2)
        # Model never calls tools, just returns prose each turn
        adapter._completion_fn = MagicMock(side_effect=[
            _make_mock_response(content="Let me think...", finish_reason="tool_calls"),
            _make_mock_response(content="I cannot do that", finish_reason="stop"),
        ])
        with pytest.raises(ParseError) as exc_info:
            adapter._call_llm_with_tools("prompt")
        err = str(exc_info.value)
        assert "submit_files" in err
        assert "I cannot do that" in err


class TestToolLoopDiagnosticFields:
    """Error message includes all required diagnostic fields."""

    def test_includes_model_name(self):
        adapter = LiteLLMAdapter(
            model="gemini-2.5-pro",
            workspace_mcp=MagicMock(),
        )
        adapter._completion_fn = MagicMock(side_effect=[
            _make_mock_response(content="No tools"),
            _make_mock_response(content="", finish_reason="stop"),
        ])
        with pytest.raises(ParseError) as exc_info:
            adapter._call_llm_with_tools("prompt")
        assert "gemini-2.5-pro" in str(exc_info.value)

    def test_includes_turns_used(self):
        adapter = LiteLLMAdapter(workspace_mcp=MagicMock())
        adapter._completion_fn = MagicMock(side_effect=[
            _make_mock_response(content="No tools"),
            _make_mock_response(content="", finish_reason="stop"),
        ])
        with pytest.raises(ParseError) as exc_info:
            adapter._call_llm_with_tools("prompt")
        assert "turns used: 2" in str(exc_info.value)

    def test_includes_finish_reason(self):
        adapter = LiteLLMAdapter(workspace_mcp=MagicMock())
        adapter._completion_fn = MagicMock(side_effect=[
            _make_mock_response(content="No tools"),
            _make_mock_response(content="", finish_reason="content_filter"),
        ])
        with pytest.raises(ParseError) as exc_info:
            adapter._call_llm_with_tools("prompt")
        assert "finish_reason: content_filter" in str(exc_info.value)

    def test_truncation_raises_parse_error_with_task_size(self):
        adapter = LiteLLMAdapter(workspace_mcp=MagicMock(), max_tokens=8000)
        adapter._completion_fn = MagicMock(return_value=_make_mock_response(
            content="Some long preamble...",
            finish_reason="max_tokens",
        ))
        with pytest.raises(ParseError) as exc_info:
            adapter._call_llm_with_tools("prompt")
        err = str(exc_info.value)
        assert "max_tokens=8000" in err
        assert "21 chars" in err
        # Observation is reported; inference is labelled as inference.
        assert "Observed:" in err
        assert "Inference (not confirmed):" in err
        assert "task is too large" in err

    def test_includes_content_preview(self):
        adapter = LiteLLMAdapter(workspace_mcp=MagicMock())
        adapter._completion_fn = MagicMock(side_effect=[
            _make_mock_response(content="No tools"),
            _make_mock_response(content="This is some unparseable prose", finish_reason="stop"),
        ])
        with pytest.raises(ParseError) as exc_info:
            adapter._call_llm_with_tools("prompt")
        assert "content preview:" in str(exc_info.value)
        assert "This is some unparseable prose" in str(exc_info.value)


class TestToolLoopTurnCapNoContent:
    """Turn cap with no assistant content at all raises diagnostic."""

    def test_no_assistant_content_raises(self):
        adapter = LiteLLMAdapter(workspace_mcp=MagicMock(), max_tool_turns=1)
        # Model returns empty content with no tool calls
        adapter._completion_fn = MagicMock(return_value=_make_mock_response(
            content=None, finish_reason="stop",
        ))
        with pytest.raises(ParseError) as exc_info:
            adapter._call_llm_with_tools("prompt")
        err = str(exc_info.value)
        assert "submit_files" in err
        assert "(empty)" in err


class TestToolLoopResponseInvariant:
    """Every tool_call_id in an assistant message gets a tool response before
    the next request — terminal tools, failing tools, tools returning nothing,
    and turns mixing several calls (Fixes #53)."""

    def _run(self, responses, workspace=None):
        adapter = LiteLLMAdapter(workspace_mcp=workspace or MagicMock())
        adapter._completion_fn = MagicMock(side_effect=responses)
        return adapter, adapter._call_llm_with_tools("prompt")

    def _last_messages(self, adapter):
        return adapter._completion_fn.call_args_list[-1].kwargs["messages"]

    def test_zero_file_submit_files_gets_tool_response(self):
        """submit_files(0 file(s)) is refused with a tool response, not a
        broken history — the loop continues and the next request is valid."""
        workspace = MagicMock()
        workspace.read_file.return_value = "old content"
        adapter, _ = self._run([
            _make_mock_response(
                content=None,
                tool_calls=[_make_submit_files_call([])],
                finish_reason="tool_calls",
            ),
            _make_mock_response(
                tool_calls=[_make_submit_files_call([
                    {"path": "src/main.py", "content": "new content"},
                ])],
            ),
        ], workspace=workspace)
        _assert_tool_call_ids_answered(self._last_messages(adapter))

    def test_unparseable_submit_files_gets_tool_response(self):
        """submit_files with invalid arguments is answered, not skipped."""
        tc = _make_submit_files_call([{"path": "a.py", "content": "x"}])
        tc.function.arguments = "not json"
        adapter, _ = self._run([
            _make_mock_response(
                content=None,
                tool_calls=[tc],
                finish_reason="tool_calls",
            ),
            _make_mock_response(
                tool_calls=[_make_submit_files_call([
                    {"path": "src/main.py", "content": "new content"},
                ])],
            ),
        ])
        _assert_tool_call_ids_answered(self._last_messages(adapter))

    def test_mixed_turn_submit_files_and_read_tool(self):
        """A turn mixing submit_files with a read tool answers both ids."""
        submit_tc = _make_submit_files_call([])
        submit_tc.id = "call_submit"
        read_tc = _make_read_file_call()
        read_tc.id = "call_read"
        adapter, _ = self._run([
            _make_mock_response(
                content=None,
                tool_calls=[submit_tc, read_tc],
                finish_reason="tool_calls",
            ),
            _make_mock_response(
                tool_calls=[_make_submit_files_call([
                    {"path": "src/main.py", "content": "new content"},
                ])],
            ),
        ])
        _assert_tool_call_ids_answered(self._last_messages(adapter))

    def test_failing_tool_gets_tool_response(self):
        """A tool that raises still gets a tool response for its id."""
        workspace = MagicMock()
        workspace.read_file.side_effect = FileNotFoundError("missing")
        adapter, _ = self._run([
            _make_mock_response(
                content=None,
                tool_calls=[_make_read_file_call("missing.py")],
                finish_reason="tool_calls",
            ),
            _make_mock_response(
                tool_calls=[_make_submit_files_call([
                    {"path": "src/main.py", "content": "new content"},
                ])],
            ),
        ], workspace=workspace)
        _assert_tool_call_ids_answered(self._last_messages(adapter))

    def test_unknown_tool_gets_tool_response(self):
        """An unknown tool name still gets a tool response for its id."""
        tc = _make_read_file_call()
        tc.function.name = "some_future_tool"
        adapter, _ = self._run([
            _make_mock_response(
                content=None,
                tool_calls=[tc],
                finish_reason="tool_calls",
            ),
            _make_mock_response(
                tool_calls=[_make_submit_files_call([
                    {"path": "src/main.py", "content": "new content"},
                ])],
            ),
        ])
        _assert_tool_call_ids_answered(self._last_messages(adapter))

    def test_zero_file_submit_files_is_not_a_completion(self):
        """submit_files(0 file(s)) must not terminate the loop as a valid
        completion — it is refused and the coder is told to produce files."""
        workspace = MagicMock()
        adapter, _ = self._run([
            _make_mock_response(
                content=None,
                tool_calls=[_make_submit_files_call([])],
                finish_reason="tool_calls",
            ),
            _make_mock_response(
                tool_calls=[_make_submit_files_call([
                    {"path": "src/main.py", "content": "new content"},
                ])],
            ),
        ], workspace=workspace)
        messages = self._last_messages(adapter)
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert any("empty files list" in m["content"] for m in tool_msgs)
        assert any("at least one file" in m["content"] for m in tool_msgs)
