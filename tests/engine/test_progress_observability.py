"""Tests for tool-loop progress observability (Issue #51).

Tests cover:
- Elapsed time and tool-call summary formatting helpers.
- Turn progress emissions from LiteLLMAdapter tool-use loop.
- Turn progress emissions from LLMValidator tool-use loop.
- LoopDriver progress callback integration.
"""

from unittest.mock import MagicMock

from snodo.coders.litellm import LiteLLMAdapter
from snodo.compiler.models import Validator
from snodo.core.interfaces import Task
from snodo.engine.progress import format_elapsed, format_tool_call_summary
from snodo.validators.context import ValidatorContext
from snodo.validators.llm_validator import LLMValidator


def test_format_elapsed():
    """Test elapsed time formatting into m:ss format."""
    assert format_elapsed(0) == "0:00"
    assert format_elapsed(4.2) == "0:04"
    assert format_elapsed(65) == "1:05"
    assert format_elapsed(3605) == "60:05"


def test_format_tool_call_summary():
    """Test formatting various tool calls into human-readable strings."""
    assert format_tool_call_summary([]) == "(no tools called)"

    # Mock tool call objects
    tc_read = MagicMock()
    tc_read.function.name = "read_file"
    tc_read.function.arguments = '{"path": "src/main.py"}'

    tc_lines = MagicMock()
    tc_lines.function.name = "read_file_lines"
    tc_lines.function.arguments = '{"path": "src/main.py", "start": 1, "end": 20}'

    tc_list = MagicMock()
    tc_list.function.name = "list_files"
    tc_list.function.arguments = '{"directory": "."}'

    tc_submit_files = MagicMock()
    tc_submit_files.function.name = "submit_files"
    tc_submit_files.function.arguments = '{"files": [{"path": "a.py"}, {"path": "b.py"}]}'

    tc_submit_verdict = MagicMock()
    tc_submit_verdict.function.name = "submit_verdict"
    tc_submit_verdict.function.arguments = '{"severity": "pass", "justification": "ok"}'

    tc_read_files = MagicMock()
    tc_read_files.function.name = "read_files"
    tc_read_files.function.arguments = '{"paths": ["src/a.py", "src/b.py"]}'

    summary = format_tool_call_summary([tc_read, tc_lines])
    assert summary == "read_file(src/main.py), read_file_lines(src/main.py:1-20)"

    summary_read_files = format_tool_call_summary([tc_read_files])
    assert summary_read_files == "read_files(src/a.py, src/b.py)"

    summary_list = format_tool_call_summary([tc_list])
    assert summary_list == "list_files(.)"

    summary_files = format_tool_call_summary([tc_submit_files])
    assert summary_files == "submit_files(2 file(s))"

    summary_verdict = format_tool_call_summary([tc_submit_verdict])
    assert summary_verdict == "submit_verdict(pass)"


def test_litellm_adapter_emits_turn_progress():
    """LiteLLMAdapter invokes progress_callback on each tool loop turn."""
    emitted = []

    def cb(msg: str):
        emitted.append(msg)

    adapter = LiteLLMAdapter(max_tool_turns=3, progress_callback=cb)
    adapter.workspace_mcp = MagicMock()

    # Mock completion_fn returning read_file tool call on turn 1, then submit_files on turn 2
    tc_read = MagicMock()
    tc_read.id = "call_1"
    tc_read.function.name = "read_file"
    tc_read.function.arguments = '{"path": "src/app.py"}'

    resp1 = MagicMock()
    resp1.choices = [MagicMock()]
    resp1.choices[0].message.content = ""
    resp1.choices[0].message.tool_calls = [tc_read]
    resp1.choices[0].finish_reason = "tool_calls"

    tc_submit = MagicMock()
    tc_submit.id = "call_2"
    tc_submit.function.name = "submit_files"
    tc_submit.function.arguments = '{"files": [{"path": "src/app.py", "content": "print(1)"}]}'

    resp2 = MagicMock()
    resp2.choices = [MagicMock()]
    resp2.choices[0].message.content = ""
    resp2.choices[0].message.tool_calls = [tc_submit]
    resp2.choices[0].finish_reason = "tool_calls"

    resp3 = MagicMock()
    resp3.choices = [MagicMock()]
    resp3.choices[0].message.content = "Done"
    resp3.choices[0].message.tool_calls = None
    resp3.choices[0].finish_reason = "stop"

    adapter._completion_fn = MagicMock(side_effect=[resp1, resp2, resp3])
    adapter._execute_tool = MagicMock(return_value="file content")

    res = adapter._call_llm_with_tools("prompt")
    assert res is not None
    assert len(emitted) == 3
    assert "Turn 1: read_file(src/app.py)" in emitted[0]
    assert "Turn 2: submit_files(1 file(s))" in emitted[1]
    assert "Turn 3: (no tools called)" in emitted[2]


def test_llm_validator_emits_turn_progress():
    """LLMValidator invokes progress_callback on each validator tool loop turn."""
    emitted = []

    def cb(msg: str):
        emitted.append(msg)

    val_spec = Validator(
        validator_id="quality",
        validator_type="quality",
        criteria=["Ensure quality"],
        tools=["read_file"],
    )
    validator = LLMValidator(validator_spec=val_spec)

    tc_submit = MagicMock()
    tc_submit.id = "val_call_1"
    tc_submit.function.name = "submit_verdict"
    tc_submit.function.arguments = '{"severity": "pass", "justification": "Looks good"}'

    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = ""
    resp.choices[0].message.tool_calls = [tc_submit]
    resp.choices[0].finish_reason = "tool_calls"

    validator._completion_fn = MagicMock(return_value=resp)

    ctx = ValidatorContext(
        task=Task(id="task_1", spec="test task"),
        completion_fn=validator._completion_fn,
        workspace_mcp=MagicMock(),
        max_tool_turns=5,
        progress_callback=cb,
    )

    result = validator._evaluate_with_tools(ctx)
    assert result.severity == "pass"
    assert len(emitted) == 1
    assert "Turn 1: submit_verdict(pass)" in emitted[0]
