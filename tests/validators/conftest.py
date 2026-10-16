"""Shared scripted-judge compatibility for the pre-subject test transcripts.

These older suites exercise reading budgets, provider fallback and acceptance
criteria, not subject rejection. Their scripted submit_verdict responses
predate the required task_id argument. Give those *fixtures* the honest
subject from the request they receive. The dedicated subject-contract suite
is deliberately not adapted: missing and wrong IDs there must reach the real
boundary unchanged. Explicit IDs in any transcript are never overwritten.
"""

import json
from functools import wraps

import pytest


@pytest.fixture(autouse=True)
def bind_legacy_scripted_judge_subject(request, monkeypatch):
    if request.path.name not in {"test_llm_validator.py", "test_acceptance.py", "test_change_context.py"}:
        return
    from snodo.validators.llm_validator import LLMValidator

    original = LLMValidator._call_completion_with_retry

    @wraps(original)
    def scripted_completion(self, *args, **kwargs):
        response = original(self, *args, **kwargs)
        marker = "The task identifier under judgement is "
        subject = None
        for message in kwargs.get("messages", []):
            content = message.get("content")
            if isinstance(content, str) and marker in content:
                subject, _ = json.JSONDecoder().raw_decode(content.split(marker, 1)[1])
                break
        if subject is None:
            return response
        for call in response.choices[0].message.tool_calls or []:
            if call.function.name != "submit_verdict":
                continue
            try:
                arguments = json.loads(call.function.arguments)
            except (TypeError, ValueError):
                continue
            if isinstance(arguments, dict) and "task_id" not in arguments:
                arguments["task_id"] = subject
                call.function.arguments = json.dumps(arguments)
        return response

    monkeypatch.setattr(LLMValidator, "_call_completion_with_retry", scripted_completion)
