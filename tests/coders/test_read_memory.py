"""Tests for repeat read memory deduplication in tool loops (Fixes #64)."""

import json
from unittest.mock import MagicMock

from snodo.coders.litellm import (
    LiteLLMAdapter,
    _canonical_read_key,
    format_repeat_read_response,
)
from snodo.core.interfaces import TaskSpec


def test_canonical_read_key():
    """_canonical_read_key normalizes arguments for read tools and ignores non-read tools."""
    key1 = _canonical_read_key("read_file", {"path": "./src/scripts/auth.js"})
    key2 = _canonical_read_key("read_file", {"path": "src/scripts/auth.js"})
    assert key1 == key2
    assert key1[0] == "read_file"

    key_lines = _canonical_read_key("read_file_lines", {"path": "main.py", "start": 1, "end": 20})
    assert key_lines is not None
    assert key_lines[0] == "read_file_lines"

    key_non_read = _canonical_read_key("submit_files", {"files": []})
    assert key_non_read is None


def test_format_repeat_read_response():
    """format_repeat_read_response generates concise turn pointers."""
    msg = format_repeat_read_response("read_file", {"path": "src/scripts/auth.js"}, 3)
    assert "File 'src/scripts/auth.js'" in msg
    assert "was already fetched using read_file in Turn 3" in msg
    assert "Refer to the tool response from Turn 3" in msg


def test_litellm_coder_serves_repeat_read_pointer():
    """LiteLLMAdapter tool loop points repeat reads to earlier turns without re-reading workspace."""
    workspace = MagicMock()
    workspace.read_file.side_effect = ["content_v1", "content_v2"]

    coder = LiteLLMAdapter(model="gpt-4o", workspace_mcp=workspace)

    # Turn 1: Model requests read_file("auth.js")
    # Turn 2: Model requests read_file("auth.js") AGAIN (repeat)
    # Turn 3: Model calls submit_files
    def mock_completion(**kwargs):
        messages = kwargs["messages"]
        num_user_tool = len(messages)

        if num_user_tool == 1:
            # Turn 1
            tc = MagicMock()
            tc.id = "call_1"
            tc.function.name = "read_file"
            tc.function.arguments = json.dumps({"path": "auth.js"})
            msg = MagicMock()
            msg.content = None
            msg.tool_calls = [tc]
            resp = MagicMock()
            resp.choices = [MagicMock(message=msg, finish_reason="tool_calls")]
            return resp
        elif num_user_tool == 3:
            # Turn 2: repeat read call
            tc = MagicMock()
            tc.id = "call_2"
            tc.function.name = "read_file"
            tc.function.arguments = json.dumps({"path": "auth.js"})
            msg = MagicMock()
            msg.content = None
            msg.tool_calls = [tc]
            resp = MagicMock()
            resp.choices = [MagicMock(message=msg, finish_reason="tool_calls")]
            return resp
        else:
            # Turn 3: submit_files
            tc = MagicMock()
            tc.id = "call_3"
            tc.function.name = "submit_files"
            tc.function.arguments = json.dumps({"files": [{"path": "auth.js", "content": "mod", "action": "write"}]})
            msg = MagicMock()
            msg.content = None
            msg.tool_calls = [tc]
            resp = MagicMock()
            resp.choices = [MagicMock(message=msg, finish_reason="stop")]
            return resp

    coder._completion_fn = mock_completion
    spec = TaskSpec(id="t1", description="test task", constraints=[])
    artifact = coder.implement(spec)

    assert len(artifact.files) == 1
    # workspace.read_file should ONLY have been called ONCE (Turn 1), not twice
    assert workspace.read_file.call_count == 1
    workspace.read_file.assert_called_once_with("auth.js")


def test_read_memory_tracker_range_coverage():
    """ReadMemoryTracker detects when ranged reads are covered by earlier full or ranged reads."""
    from snodo.coders.litellm import ReadMemoryTracker

    tracker = ReadMemoryTracker()

    # Turn 1: read_file("src/app.py") (full file)
    tracker.record_read("read_file", {"path": "src/app.py"}, turn_idx=1)

    # Turn 2: read_file_lines("src/app.py", start=10, end=50) -> covered by Turn 1!
    assert tracker.check_read("read_file_lines", {"path": "src/app.py", "start": 10, "end": 50}) == 1

    # Turn 3: read_file_lines("src/utils.py", start=1, end=400)
    tracker.record_read("read_file_lines", {"path": "src/utils.py", "start": 1, "end": 400}, turn_idx=3)

    # Turn 4: read_file_lines("src/utils.py", start=50, end=150) -> covered by Turn 3!
    assert tracker.check_read("read_file_lines", {"path": "src/utils.py", "start": 50, "end": 150}) == 3

    # Turn 5: read_file_lines("src/utils.py", start=100, end=500) -> NOT fully covered
    assert tracker.check_read("read_file_lines", {"path": "src/utils.py", "start": 100, "end": 500}) is None


def test_read_memory_tracker_list_files_dedup():
    """ReadMemoryTracker deduplicates repeated list_files directory listings."""
    from snodo.coders.litellm import ReadMemoryTracker

    tracker = ReadMemoryTracker()

    # Turn 1: list_files("src")
    tracker.record_read("list_files", {"directory": "src"}, turn_idx=1)

    # Turn 2: list_files("src") -> covered by Turn 1!
    assert tracker.check_read("list_files", {"directory": "src"}) == 1
    assert tracker.check_read("list_files", {"path": "./src/"}) == 1
