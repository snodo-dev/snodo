"""Regression tests for recovery from length-truncated recon responses."""

from types import SimpleNamespace

import pytest
from unittest.mock import patch

from tests.recon.test_recon import _reading_response


@pytest.fixture
def project_with_snodo(tmp_path):
    (tmp_path / ".snodo").mkdir()
    return str(tmp_path)


def _response(content="", tool_calls=None, finish_reason="stop", reasoning=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    if reasoning is not None:
        message.reasoning = reasoning
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])


def _submit(arguments):
    return SimpleNamespace(
        id="answer",
        function=SimpleNamespace(name="submit_answer", arguments=arguments),
    )


def test_empty_length_on_nonfinal_turn_reaches_forced_answer(project_with_snodo):
    from snodo.recon import call_agent

    cut_off = _response(finish_reason="length")
    answer = "Recovered after the context read."
    with patch("litellm.completion", side_effect=[
        _reading_response("Found useful context.", "a.py"), cut_off,
        _response(answer),
    ]) as completion:
        result = call_agent(project_with_snodo, "test/model", "query", ["./"], "agent", max_turns=3)

    assert completion.call_count == 3
    assert result.result.startswith(answer)
    assert "finish_reason=length" in result.result
    assert "finish_reason=length" in result.error
    assert completion.call_args_list[2].kwargs["tools"][0]["function"]["name"] == "submit_answer"
    assert completion.call_args_list[2].kwargs["tool_choice"]["function"]["name"] == "submit_answer"


def test_cut_off_submit_payload_returns_leading_text(project_with_snodo):
    from snodo.recon import call_agent

    cut_off = _response(tool_calls=[_submit('{"answer": "Leading recovered answer')], finish_reason="length")
    with patch("litellm.completion", side_effect=[
        _reading_response("Context.", "a.py"), cut_off,
    ]):
        result = call_agent(project_with_snodo, "test/model", "query", ["./"], "agent", max_turns=1)

    assert result.result.startswith("Leading recovered answer")
    assert "finish_reason=length" in result.result
    assert result.trace["ended"] == "truncated:length"
    assert "finish_reason=length" in result.error


def test_unrecoverable_submit_payload_returns_no_answer(project_with_snodo):
    from snodo.recon import call_agent

    cut_off = _response(tool_calls=[_submit('{"answer":')], finish_reason="length")
    with patch("litellm.completion", side_effect=[
        cut_off, _response(), _response(),
    ]):
        result = call_agent(project_with_snodo, "test/model", "query", ["./"], "agent", max_turns=1)

    assert result.result == ""
    assert result.error
    assert result.trace["ended"].startswith("truncated:")


def test_empty_forced_retry_keeps_narration_note_error_and_true_ending(project_with_snodo):
    from snodo.recon import call_agent

    with patch("litellm.completion", side_effect=[
        _reading_response("Earlier useful narration.", "a.py"),
        _response(finish_reason="length"), _response(finish_reason="stop"),
    ]) as completion:
        result = call_agent(project_with_snodo, "test/model", "query", ["./"], "agent", max_turns=2)

    assert completion.call_count == 3
    assert result.result.startswith("Earlier useful narration.")
    assert "finish_reason=length" in result.result
    assert "finish_reason=length" in result.error
    assert result.trace["ended"] == "truncated:length"
    assert "empty_final" not in result.error
    assert "empty_final" not in result.trace["ended"]


def test_reasoning_only_reply_is_not_an_answer(project_with_snodo):
    from snodo.recon import call_agent

    reasoning_only = _response(reasoning="private reasoning", finish_reason="stop")
    with patch("litellm.completion", side_effect=[reasoning_only, reasoning_only]) as completion:
        result = call_agent(project_with_snodo, "test/model", "query", ["./"], "agent", max_turns=1)

    assert completion.call_count == 1
    assert result.result == ""
    assert result.error == "Final answer unavailable; run ended with prose."
    assert result.trace["ended"] == "prose"
    assert "private reasoning" not in result.result
