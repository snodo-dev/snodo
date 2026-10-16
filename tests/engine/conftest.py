"""Compatibility for the legacy turn-progress judge transcript."""

import json

import pytest


@pytest.fixture(autouse=True)
def bind_progress_judge_subject(request, monkeypatch):
    if request.path.name != "test_progress_observability.py":
        return
    from snodo.validators.llm_validator import LLMValidator

    original = LLMValidator._call_completion_with_retry

    def complete(self, *args, **kwargs):
        response = original(self, *args, **kwargs)
        for call in response.choices[0].message.tool_calls or []:
            if call.function.name == "submit_verdict":
                arguments = json.loads(call.function.arguments)
                arguments.setdefault("task_id", "task_1")
                call.function.arguments = json.dumps(arguments)
        return response

    monkeypatch.setattr(LLMValidator, "_call_completion_with_retry", complete)
