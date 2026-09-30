import json

import pytest

from snodo.coders.agy_adapter import AGYAdapter
from snodo.coders.codex_cli_adapter import CodexCLIAdapter
from snodo.coders.opencode_cli_adapter import OpenCodeCLIAdapter


@pytest.mark.parametrize(
    "adapter_type, events",
    [
        (
            OpenCodeCLIAdapter,
            [
                {"type": "text", "part": {"text": "Working"}},
                {"type": "step_finish", "part": {"tokens": {"input": 9}}},
                {"type": "text", "part": {"text": "Implemented X"}},
            ],
        ),
        (
            CodexCLIAdapter,
            [
                {"type": "item.completed", "item": {"type": "agent_message", "text": "Working"}},
                {"type": "turn.completed", "usage": {"input_tokens": 9}},
                {"type": "item.completed", "item": {"type": "agent_message", "text": "Implemented X"}},
            ],
        ),
    ],
)
def test_json_stream_tail_contains_messages_not_raw_events(tmp_path, adapter_type, events):
    adapter = adapter_type(workspace=tmp_path)
    stdout = "\n".join(json.dumps(event) for event in events)
    tail = adapter._combined_output_tail(stdout, "rate limit diagnostic")

    assert "Implemented X" in tail
    assert "rate limit diagnostic" in tail
    assert "step_finish" not in tail
    assert "turn.completed" not in tail
    assert adapter.last_usage["input_tokens"] is None


def test_plain_text_tail_is_unchanged(tmp_path):
    adapter = AGYAdapter(workspace=tmp_path)
    stdout = "first line\nfinal human message"

    assert adapter._combined_output_tail(stdout, "") == stdout
