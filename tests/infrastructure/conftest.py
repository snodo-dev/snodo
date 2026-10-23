"""Bind legacy telemetry transcripts to their honest task subject.

Telemetry tests predate submit_verdict.task_id; they inspect provenance and
turn records, while validators/test_verdict_subject.py covers rejection.
"""

import json

import pytest


@pytest.fixture(autouse=True)
def bind_telemetry_judge_subject(request, monkeypatch):
    if request.path.name not in {"test_tool_telemetry.py", "test_served_model_provenance.py"}:
        return
    from snodo.validators.llm_validator import LLMValidator

    original = LLMValidator._call_completion_with_retry

    def complete(self, *args, **kwargs):
        response = original(self, *args, **kwargs)
        for call in response.choices[0].message.tool_calls or []:
            if call.function.name == "submit_verdict":
                arguments = json.loads(call.function.arguments)
                arguments["task_id"] = self._task_id
                call.function.arguments = json.dumps(arguments)
        return response

    monkeypatch.setattr(LLMValidator, "_call_completion_with_retry", complete)
