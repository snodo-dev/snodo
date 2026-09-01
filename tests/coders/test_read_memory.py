"""Tests for repeat read memory deduplication in tool loops (Fixes #64)."""

import json
from unittest.mock import MagicMock

from snodo.coders.litellm import (
    LiteLLMAdapter,
    _canonical_read_key,
    format_repeat_read_response,
)
from snodo.core.interfaces import TaskSpec
from snodo.tools.workspace import WorkspaceMCP


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


def test_repeated_identical_search_does_not_repeat():
    """A search_string call repeated across turns dedupes, regardless of how the
    optional default `directory` is spelled (Fixes #184)."""
    from snodo.coders.litellm import ReadMemoryTracker

    tracker = ReadMemoryTracker()
    tracker.record_read("search_string", {"query": "tests"}, turn_idx=5)

    assert tracker.check_read("search_string", {"query": "tests"}) == 5
    assert tracker.check_read("search_string", {"query": "tests", "directory": "."}) == 5
    assert tracker.check_read("search_string", {"query": "tests", "directory": "./"}) == 5
    assert tracker.check_read("search_string", {"query": "tests", "directory": ""}) == 5
    assert tracker.check_read("search_string", {"query": "tests", "directory": None}) == 5

    # A genuinely different search must still run.
    assert tracker.check_read("search_string", {"query": "src/scripts"}) is None
    assert tracker.check_read("search_string", {"query": "tests", "directory": "src"}) is None


def test_repeated_identical_symbol_search_does_not_repeat():
    """search_symbol repeats dedupe too, including argument-order spelling."""
    from snodo.coders.litellm import ReadMemoryTracker

    tracker = ReadMemoryTracker()
    tracker.record_read("search_symbol", {"name": "AuthManager", "directory": "src"}, turn_idx=7)

    assert tracker.check_read("search_symbol", {"name": "AuthManager", "directory": "src"}) == 7
    assert tracker.check_read("search_symbol", {"directory": "src", "name": "AuthManager"}) == 7
    assert tracker.check_read("search_symbol", {"name": "AuthManager"}) is None


def test_two_spellings_of_same_path_dedupe_to_single_read(tmp_path):
    """Every spelling the workspace resolver unifies dedupes to one read (Fixes #184)."""
    from snodo.coders.litellm import ReadMemoryTracker

    target = tmp_path / "src" / "scripts" / "main.js"
    target.parent.mkdir(parents=True)
    target.write_text("console.log(1)\n")

    tracker = ReadMemoryTracker(project_root=str(tmp_path))
    tracker.record_read("read_file", {"path": "src/scripts/main.js"}, turn_idx=9)

    # Same call and alias spellings of the same file — all covered by Turn 9.
    assert tracker.check_read("read_file", {"path": "src/scripts/main.js"}) == 9
    assert tracker.check_read("read_file", {"path": "./src/scripts/main.js"}) == 9
    assert tracker.check_read("read_file", {"path": "src/scripts//main.js"}) == 9
    assert tracker.check_read("read_file", {"path": "src\\scripts\\main.js"}) == 9
    assert tracker.check_read("read_file", {"path": str(target)}) == 9

    # Batch reads dedupe via the per-file coverage check on aliased paths too.
    assert tracker.check_read("read_files", {"paths": ["./src/scripts/main.js"]}) == 9

    # A different file is not covered.
    assert tracker.check_read("read_file", {"path": "src/other.js"}) is None


def test_read_files_batch_key_dedupes_across_path_spellings(tmp_path):
    """read_files with an aliased path list matches the recorded batch exactly."""
    from snodo.coders.litellm import ReadMemoryTracker

    tracker = ReadMemoryTracker(project_root=str(tmp_path))
    tracker.record_read("read_files", {"paths": ["a.js", "b.js"]}, turn_idx=3)
    assert tracker.check_read("read_files", {"paths": ["./a.js", "./b.js"]}) == 3


def test_search_repeat_response_names_the_term():
    """The dedupe pointer for searches names the term and says not to repeat it."""
    msg = format_repeat_read_response("search_string", {"query": "tests"}, 12)
    assert "search_string for 'tests' was already run in Turn 12" in msg
    assert "wastes a turn" in msg


def test_directory_in_search_term_gets_guidance_not_a_scan(tmp_path):
    """A directory passed in the search-term slot returns a correction instead of
    scanning the tree (Fixes #184)."""
    from snodo.tools.workspace import WorkspaceMCP

    proj = tmp_path / "proj"
    (proj / "src" / "scripts").mkdir(parents=True)
    (proj / "src" / "scripts" / "main.js").write_text("HELLO main\n")
    ws = WorkspaceMCP(str(proj))

    real = ws.search_string
    spy = MagicMock(side_effect=real)
    ws.search_string = spy

    out = LiteLLMAdapter._execute_tool("search_string", {"query": "src/scripts"}, ws)
    assert spy.call_count == 0
    assert "'src/scripts' is a directory" in out
    assert 'directory="src/scripts"' in out

    # A legitimate text search still executes.
    out2 = LiteLLMAdapter._execute_tool("search_string", {"query": "HELLO"}, ws)
    assert spy.call_count == 1
    assert "main.js:1" in out2

    # An explicit, different directory alongside a path-like term is a
    # legitimate scoped search and still executes.
    out3 = LiteLLMAdapter._execute_tool(
        "search_string", {"query": "src", "directory": "src/scripts"}, ws
    )
    assert spy.call_count == 2
    assert "is a directory" not in out3


def test_tool_loop_repeated_search_executes_workspace_once(tmp_path):
    """End-to-end: the same logical search across turns hits the workspace once;
    the repeats come back as turn pointers."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "main.py").write_text("NEEDLE = 1\n")
    workspace = WorkspaceMCP(str(proj))
    real = workspace.search_string
    spy = MagicMock(side_effect=real)
    workspace.search_string = spy

    coder = LiteLLMAdapter(model="gpt-4o", workspace_mcp=workspace, max_tool_turns=6)
    seen_messages: list = []

    def _tc(cid, name, args):
        tc = MagicMock()
        tc.id = cid
        tc.function.name = name
        tc.function.arguments = json.dumps(args)
        return tc

    def mock_completion(**kwargs):
        messages = kwargs["messages"]
        seen_messages.append(list(messages))
        n = len(messages)
        if n == 1:
            tcs = [_tc("c1", "search_string", {"query": "NEEDLE"})]
        elif n == 3:
            tcs = [_tc("c2", "search_string", {"query": "NEEDLE", "directory": "."})]
        elif n == 5:
            tcs = [_tc("c3", "search_string", {"query": "NEEDLE", "directory": None})]
        else:
            tcs = [_tc("c4", "submit_files", {"files": [{"path": "main.py", "content": "NEEDLE = 2"}]})]
        msg = MagicMock()
        msg.content = None
        msg.tool_calls = tcs
        resp = MagicMock()
        resp.choices = [MagicMock(message=msg, finish_reason="tool_calls")]
        resp.usage = None
        return resp

    coder._completion_fn = mock_completion
    spec = TaskSpec(id="t1", description="test task", constraints=[])
    artifact = coder.implement(spec)

    assert len(artifact.files) == 1
    assert spy.call_count == 1
    # The last completion request carries the whole conversation so far.
    final_msgs = seen_messages[-1]
    tool_msgs = [m["content"] for m in final_msgs if m.get("role") == "tool"]
    repeats = [t for t in tool_msgs if "already run in Turn 1" in t]
    assert len(repeats) == 2


def test_tool_loop_wrong_slot_search_guides_then_dedupes(tmp_path):
    """The directory-in-query misuse costs one guidance response, not a scan,
    and its repetition dedupes from there."""
    proj = tmp_path / "proj"
    (proj / "tests").mkdir(parents=True)
    workspace = WorkspaceMCP(str(proj))
    spy = MagicMock(return_value="no matches")
    workspace.search_string = spy

    coder = LiteLLMAdapter(model="gpt-4o", workspace_mcp=workspace, max_tool_turns=6)

    def _tc(cid, name, args):
        tc = MagicMock()
        tc.id = cid
        tc.function.name = name
        tc.function.arguments = json.dumps(args)
        return tc

    def mock_completion(**kwargs):
        n = len(kwargs["messages"])
        if n == 1:
            tcs = [_tc("c1", "search_string", {"query": "tests"})]
        elif n == 3:
            tcs = [_tc("c2", "search_string", {"query": "tests"})]
        else:
            tcs = [_tc("c3", "submit_files", {"files": [{"path": "x.py", "content": "x = 1"}]})]
        msg = MagicMock()
        msg.content = None
        msg.tool_calls = tcs
        resp = MagicMock()
        resp.choices = [MagicMock(message=msg, finish_reason="tool_calls")]
        resp.usage = None
        return resp

    coder._completion_fn = mock_completion
    spec = TaskSpec(id="t1", description="test task", constraints=[])
    artifact = coder.implement(spec)

    assert len(artifact.files) == 1
    # The wrong call never scanned the tree; its repeat was served from memory.
    assert spy.call_count == 0
