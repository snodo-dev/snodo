"""Regression for provider binding on the deferred acceptance pass (#993)."""

from functools import partial
from types import SimpleNamespace
from unittest.mock import MagicMock

from snodo.compiler.models import Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.engine.validators import ValidatorRunner
from snodo.validators import llm_validator


DEFAULT_MODEL = "default-provider/default-judge"
PINNED_MODEL = "compatible-provider/pinned-judge"
DEFAULT_BASE = "https://default.invalid/v1"
PINNED_BASE = "https://compatible.invalid/v1"
DEFAULT_KEY = "test-default-key"
PINNED_KEY = "test-compatible-key"


def test_deferred_acceptance_uses_its_pinned_provider(monkeypatch):
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
    monkeypatch.setattr(llm_validator, "supports_response_schema", lambda _model: False)

    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        message = SimpleNamespace(
            content='{"severity":"pass","justification":"criteria met"}',
            tool_calls=[],
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    completion_fn = MagicMock(side_effect=completion)
    bound_completion = partial(
        completion_fn, model=DEFAULT_MODEL, api_base=DEFAULT_BASE, api_key=DEFAULT_KEY
    )
    protocol = MagicMock()
    protocol.get_mode.return_value = None
    runner = ValidatorRunner(
        protocol, bound_completion, DEFAULT_MODEL,
        SimpleNamespace(max_tokens=128, max_tool_turns=3),
        None, MagicMock(), MagicMock(), None,
    )

    # Quality is required to put acceptance on the deferred pass; neutralize its execution.
    dispatch = runner._dispatch_one

    def dispatch_with_quality_stub(validator, context, registry):
        if validator.validator_type == "quality":
            return ValidatorResult(
                validator_id=validator.validator_id, severity="pass", justification="stubbed"
            )
        return dispatch(validator, context, registry)

    monkeypatch.setattr(runner, "_dispatch_one", dispatch_with_quality_stub)

    quality = Validator(
        validator_id="quality", validator_type="quality", criteria=["passes tests"]
    )
    default = Validator(
        validator_id="default", validator_type="security", criteria=["check default"]
    )
    acceptance = Validator(
        validator_id="acceptance", validator_type="acceptance",
        model=PINNED_MODEL, criteria=["meets requirements"],
    )

    runner.run(
        Task(id="task-1", spec="Acceptance criteria: meets requirements"),
        [quality, default, acceptance], None, current_mode="producer", phase="post_execute",
    )

    default_calls = [call for call in calls if call.get("model") == DEFAULT_MODEL]
    acceptance_calls = [call for call in calls if call.get("model") == PINNED_MODEL]
    assert default_calls
    assert all(call["api_base"] == DEFAULT_BASE for call in default_calls)
    assert all(call["api_key"] == DEFAULT_KEY for call in default_calls)
    assert acceptance_calls
    assert all(call["api_base"] == PINNED_BASE for call in acceptance_calls)
    assert all(call["api_key"] == PINNED_KEY for call in acceptance_calls)
    assert all(call["model"] != DEFAULT_MODEL for call in acceptance_calls)
    assert all(call["api_base"] != DEFAULT_BASE for call in acceptance_calls)
    assert all(call["api_key"] != DEFAULT_KEY for call in acceptance_calls)
