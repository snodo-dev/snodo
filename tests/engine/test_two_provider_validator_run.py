"""End-to-end regression for per-validator provider binding (#990)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from snodo.compiler.models import Validator
from snodo.core.interfaces import Task
from snodo.engine.validators import ValidatorRunner
from snodo.validators import llm_validator


DEFAULT_MODEL = "default-provider/default-judge"
PINNED_MODEL = "compatible-provider/pinned-judge"
DEFAULT_BASE = "https://default.invalid/v1"
PINNED_BASE = "https://compatible.invalid/v1"
DEFAULT_KEY = "test-default-key"
PINNED_KEY = "test-compatible-key"


@pytest.mark.parametrize("path", ["tool", "plain", "structured"])
def test_pinned_validator_calls_own_provider_on_every_call_path(monkeypatch, path):
    from snodo.config import ConfigManager

    monkeypatch.setattr(ConfigManager, "resolve_litellm_model", lambda model: model)
    monkeypatch.setattr(
        ConfigManager, "resolve_api_base",
        lambda model: PINNED_BASE if model == PINNED_MODEL else DEFAULT_BASE,
    )
    monkeypatch.setattr(
        ConfigManager, "get_key_for_model",
        lambda _self, model: PINNED_KEY if model == PINNED_MODEL else DEFAULT_KEY,
    )
    monkeypatch.setattr(llm_validator, "supports_response_schema", lambda _model: path == "structured")

    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        if kwargs.get("tools"):
            if len([call for call in calls if call.get("tools")]) == 1:
                call = SimpleNamespace(
                    id="read-1",
                    function=SimpleNamespace(name="read_file", arguments='{"path":"README.md"}'),
                )
            else:
                call = SimpleNamespace(
                    id="verdict-1",
                    function=SimpleNamespace(
                        name="submit_verdict",
                        arguments='{"task_id":"task-1","severity":"pass","justification":"ok"}',
                    ),
                )
            message = SimpleNamespace(content=None, tool_calls=[call])
        else:
            message = SimpleNamespace(content='{"severity":"pass","justification":"ok"}', tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    completion_fn = MagicMock(side_effect=completion)
    # The runner's rebinding path deliberately extracts .func from a partial.
    import functools
    bound = functools.partial(completion_fn, model=DEFAULT_MODEL, api_base=DEFAULT_BASE, api_key=DEFAULT_KEY)
    protocol = MagicMock()
    protocol.get_mode.return_value = None
    config = SimpleNamespace(max_tokens=128, max_tool_turns=3)
    runner = ValidatorRunner(protocol, bound, DEFAULT_MODEL, config, None, MagicMock(), MagicMock(), None)

    pinned = Validator(
        validator_id="pinned", validator_type="security", model=PINNED_MODEL,
        criteria=["check"], tools=["read_file"] if path == "tool" else [],
    )
    default = Validator(validator_id="default", validator_type="security", criteria=["check"])
    validators = [pinned, default] if path == "plain" else [pinned]
    runner.run(Task(id="task-1", spec="test"), validators, None, current_mode="producer")

    expected_pinned = [call for call in calls if call.get("model") == PINNED_MODEL]
    assert expected_pinned
    assert all(call["api_base"] == PINNED_BASE and call["api_key"] == PINNED_KEY for call in expected_pinned)
    assert all(call["model"] != DEFAULT_MODEL and call["api_base"] != DEFAULT_BASE and call["api_key"] != DEFAULT_KEY for call in expected_pinned)
    if path == "plain":
        default_calls = [call for call in calls if call.get("model") == DEFAULT_MODEL]
        assert len(default_calls) == 1
        assert default_calls[0]["api_base"] == DEFAULT_BASE
        assert default_calls[0]["api_key"] == DEFAULT_KEY


def test_unbindable_pinned_validator_errors_without_blocking_default_sibling(monkeypatch):
    protocol = MagicMock()
    protocol.get_mode.return_value = None
    config = SimpleNamespace(max_tokens=128, max_tool_turns=3)
    default_calls = []

    def default_completion(**kwargs):
        default_calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"severity":"pass","justification":"ok"}', tool_calls=[]
        ))])

    runner = ValidatorRunner(protocol, default_completion, DEFAULT_MODEL, config, None,
                             MagicMock(), MagicMock(), None)
    validators = [
        Validator(validator_id="pinned", validator_type="security", model=PINNED_MODEL, criteria=["check"]),
        Validator(validator_id="default", validator_type="security", criteria=["check"]),
    ]
    results = runner.run(Task(id="task-1", spec="test"), validators, None, current_mode="producer")
    assert results[0].error is True
    assert "pinned" in results[0].justification and PINNED_MODEL in results[0].justification
    assert results[1].error is False
    assert len(default_calls) == 1
    assert "model" not in default_calls[0]  # shared function remains untouched
