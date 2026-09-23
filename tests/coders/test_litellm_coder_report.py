"""The litellm coder fills its own run report from what its loop holds (#317).

The tool loop already knows the files it staged, the turns it used against its
budget, the tokens each response reported, how long it ran, and which of its
existing exits it took. These tests pin the report it assembles from those
facts: the right stop reason and turn count on a normal end, on the turn
budget, on a provider fault and on truncation, and that a report which cannot
be built never fails the run.

Nothing reads the report yet; producing a truthful one is the whole ticket.
"""

import json
from unittest.mock import MagicMock

import pytest

from snodo.coders.base import LLMCallError, ParseError, TurnBudgetExhausted
from snodo.coders.litellm import LiteLLMAdapter


def _response(content=None, tool_calls=None, finish_reason="stop", usage=None):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.choices[0].message.tool_calls = tool_calls or []
    resp.choices[0].finish_reason = finish_reason
    resp.usage = usage
    return resp


def _usage(prompt_tokens, completion_tokens):
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    return usage


def _submit(files, call_id="call_submit"):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = "submit_files"
    tc.function.arguments = json.dumps({"files": files})
    return tc


def _read(path="src/existing.py", call_id="call_read"):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = "read_file"
    tc.function.arguments = json.dumps({"path": path})
    return tc


def test_a_normal_two_turn_run_reports_completed():
    adapter = LiteLLMAdapter(workspace_mcp=MagicMock())
    adapter._completion_fn = MagicMock(side_effect=[
        _response(
            tool_calls=[_submit([{"path": "src/main.py", "content": "x"}])],
            finish_reason="tool_calls",
            usage=_usage(10, 5),
        ),
        _response(content="Done", finish_reason="stop", usage=_usage(20, 3)),
    ])

    result = adapter._call_llm_with_tools("prompt")

    assert json.loads(result)[0]["path"] == "src/main.py"
    report = adapter.last_report
    assert report is not None
    assert report.stop_reason == "completed"
    assert report.turns_used == 2
    assert report.turns_available == 20
    assert report.tokens_used == 38
    assert [change.path for change in report.files] == ["src/main.py"]
    assert report.wall_time_ms is not None


def test_a_run_ending_on_the_turn_budget_reports_turn_budget():
    adapter = LiteLLMAdapter(workspace_mcp=MagicMock(), max_tool_turns=2)
    adapter._completion_fn = MagicMock(side_effect=[
        _response(tool_calls=[_read("a.py")], finish_reason="tool_calls"),
        _response(tool_calls=[_read("b.py")], finish_reason="tool_calls"),
    ])

    with pytest.raises(TurnBudgetExhausted):
        adapter._call_llm_with_tools("prompt")

    report = adapter.last_report
    assert report is not None
    assert report.stop_reason == "turn_budget"
    assert report.turns_used == 2
    assert report.turns_available == 2


def test_a_run_ending_on_a_provider_fault_reports_provider_fault():
    adapter = LiteLLMAdapter(workspace_mcp=MagicMock())
    adapter._completion_fn = MagicMock(side_effect=RuntimeError("provider blew up"))

    with pytest.raises(LLMCallError):
        adapter._call_llm_with_tools("prompt")

    report = adapter.last_report
    assert report is not None
    assert report.stop_reason == "provider_fault"
    assert report.turns_used == 1
    assert report.turns_available == 20


def test_tool_loop_retries_rejected_parameter_and_keeps_it_dropped(caplog):
    from litellm.exceptions import BadRequestError

    adapter = LiteLLMAdapter(workspace_mcp=MagicMock())
    calls = []

    def complete(**kwargs):
        calls.append(kwargs.copy())
        if len(calls) == 1:
            raise BadRequestError(
                message="Unsupported parameter: 'temperature' is not supported with this model",
                model="custom/model",
                llm_provider="custom",
                response=None,
            )
        if len(calls) == 2:
            return _response(tool_calls=[_read()], finish_reason="tool_calls")
        if len(calls) == 3:
            return _response(
                tool_calls=[_submit([{"path": "src/main.py", "content": "x"}])],
                finish_reason="tool_calls",
            )
        return _response(content="Done")

    adapter._completion_fn = MagicMock(side_effect=complete)

    result = adapter._call_llm_with_tools("prompt")

    assert json.loads(result)[0]["path"] == "src/main.py"
    assert len(calls) == 4
    assert "temperature" in calls[0]
    assert all("temperature" not in call for call in calls[1:])
    assert "provider rejected parameter temperature" in caplog.text


def test_tool_loop_does_not_retry_provider_error_without_named_parameter():
    from litellm.exceptions import BadRequestError

    adapter = LiteLLMAdapter(workspace_mcp=MagicMock())
    adapter._completion_fn = MagicMock(side_effect=BadRequestError(
        message="The request is invalid",
        model="custom/model",
        llm_provider="custom",
        response=None,
    ))

    with pytest.raises(LLMCallError, match="tool-loop error"):
        adapter._call_llm_with_tools("prompt")

    adapter._completion_fn.assert_called_once()


def test_a_run_ending_on_truncation_reports_context_budget():
    adapter = LiteLLMAdapter(workspace_mcp=MagicMock(), max_tokens=8000)
    adapter._completion_fn = MagicMock(return_value=_response(
        content="cut off mid-", finish_reason="max_tokens",
    ))

    with pytest.raises(ParseError):
        adapter._call_llm_with_tools("prompt")

    report = adapter.last_report
    assert report is not None
    assert report.stop_reason == "context_budget"
    assert report.turns_used == 1


def test_a_report_that_cannot_be_built_never_fails_the_run(monkeypatch):
    adapter = LiteLLMAdapter(workspace_mcp=MagicMock())
    adapter._completion_fn = MagicMock(side_effect=[
        _response(
            tool_calls=[_submit([{"path": "src/main.py", "content": "x"}])],
            finish_reason="tool_calls",
        ),
        _response(content="Done", finish_reason="stop"),
    ])

    def _cannot_build(*args, **kwargs):
        raise RuntimeError("report shattered")

    monkeypatch.setattr("snodo.coders.litellm.assemble_coder_report", _cannot_build)

    result = adapter._call_llm_with_tools("prompt")

    # The run completes exactly as it did before the report existed...
    assert json.loads(result)[0]["path"] == "src/main.py"
    # ...and the report is simply absent rather than a new way to fail.
    assert adapter.last_report is None
