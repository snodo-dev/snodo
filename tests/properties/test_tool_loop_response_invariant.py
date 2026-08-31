"""Property-based test: every tool_call_id gets a tool response.

FILE: tests/properties/test_tool_loop_response_invariant.py (Fixes #53)

The tool loop must answer every tool_call_id in an assistant message with a
tool message before the next request — terminal tools, failing tools, tools
returning nothing, and turns mixing several calls. This property drives the
loop with arbitrary tool-call turns and asserts the invariant on every
request the loop makes, so any future tool that can skip its response fails
the test.
"""

import json
from unittest.mock import MagicMock

import pytest
from hypothesis import given
from hypothesis import strategies as st
from snodo.coders.litellm import LiteLLMAdapter

from tests.strategies import hypothesis_settings

_HYP_SETTINGS = hypothesis_settings()

_TOOL_NAMES = ["read_file", "read_file_lines", "list_files", "submit_files"]


def _assert_tool_call_ids_answered(messages):
    """Assert every tool_call_id in an assistant message is answered by a
    tool message before the next request."""
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


def _make_response(tool_calls):
    """Build a mock litellm response carrying the given tool calls."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = None
    response.choices[0].message.tool_calls = tool_calls
    response.choices[0].finish_reason = "tool_calls"
    return response


def _make_tool_call(name, call_id, arguments):
    """Build a mock tool call object."""
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = arguments
    return tc


@st.composite
def tool_call_turns(draw):
    """Generate a list of tool-call turns (each a list of tool calls)."""
    n_turns = draw(st.integers(1, 4))
    turns = []
    for _ in range(n_turns):
        n_calls = draw(st.integers(1, 3))
        calls = []
        for i in range(n_calls):
            name = draw(st.sampled_from(_TOOL_NAMES))
            call_id = f"call_{len(turns)}_{i}"
            if name == "read_file":
                args = json.dumps({"path": draw(st.text(min_size=1, max_size=20))})
            elif name == "read_file_lines":
                args = json.dumps({
                    "path": draw(st.text(min_size=1, max_size=20)),
                    "start": draw(st.integers(1, 100)),
                    "end": draw(st.integers(1, 100)),
                })
            elif name == "list_files":
                args = json.dumps({"directory": draw(st.text(min_size=0, max_size=20))})
            else:  # submit_files
                # Sometimes emit malformed arguments — the loop must still
                # answer the tool_call_id (Fixes #53).
                malformed = draw(st.booleans())
                if malformed:
                    args = draw(st.sampled_from([
                        "not json",
                        json.dumps({"files": "not-a-list"}),
                        json.dumps({"nope": []}),
                    ]))
                else:
                    n_files = draw(st.integers(0, 2))
                    files = [
                        {
                            "path": draw(st.text(min_size=1, max_size=20)),
                            "content": draw(st.text(min_size=1, max_size=20)),
                        }
                        for _ in range(n_files)
                    ]
                    args = json.dumps({"files": files})
            calls.append(_make_tool_call(name, call_id, args))
        turns.append(calls)
    return turns


@given(turns=tool_call_turns())
@_HYP_SETTINGS
@pytest.mark.property
def test_every_tool_call_id_gets_a_response(turns):
    """No matter what tool calls the model makes, every tool_call_id in an
    assistant message is answered before the next request."""
    workspace = MagicMock()
    workspace.read_file.return_value = "file content"
    workspace.read_file_lines.return_value = "lines"
    workspace.list_files.return_value = ["a.py", "b.py"]

    responses = [_make_response(turn) for turn in turns]
    # Final tool call to stage changes, followed by a completion response with no tool calls.
    responses.append(_make_response([
        _make_tool_call(
            "submit_files", "call_final",
            json.dumps({"files": [{"path": "a.py", "content": "x"}]}),
        )
    ]))
    responses.append(_make_response([]))

    adapter = LiteLLMAdapter(workspace_mcp=workspace)
    adapter._completion_fn = MagicMock(side_effect=responses)
    adapter._call_llm_with_tools("prompt")

    for call in adapter._completion_fn.call_args_list:
        _assert_tool_call_ids_answered(call.kwargs["messages"])
