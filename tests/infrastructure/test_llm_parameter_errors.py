"""Tests for shared provider request parameter adaptation."""

import pytest

from snodo.infrastructure import llm_parameter_errors as errors


@pytest.fixture(autouse=True)
def clear_model_memory():
    errors._MODEL_PARAMETER_VALUES.clear()


def rejection(message: str) -> Exception:
    error = RuntimeError(message)
    error.status_code = 400
    return error


@pytest.mark.parametrize(
    "message",
    [
        "BadRequestError: OpenAIException - Function tools with reasoning_effort are not supported for gpt-6-sol in /v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort to 'none'.",
        'set reasoning_effort to "none"',
    ],
)
def test_requested_allowlisted_value_is_set_and_remembered(message):
    kwargs = {"tools": []}
    adapted = set()
    model = "gateway/gpt-6-sol"

    assert errors.remove_rejected_parameter(rejection(message), kwargs, adapted, model) == "reasoning_effort"
    assert kwargs["reasoning_effort"] == "none"

    next_request = {}
    errors.apply_remembered_parameters(model, next_request)
    assert next_request == {"reasoning_effort": "none"}


def test_non_allowlisted_requested_value_is_not_applied():
    kwargs = {}
    assert errors.remove_rejected_parameter(
        rejection("set api_key to 'secret'"), kwargs, set(), "model"
    ) is None
    assert kwargs == {}


def test_requested_value_is_adapted_at_most_once_per_run():
    kwargs = {}
    adapted = set()
    error = rejection("set reasoning_effort to 'none'")
    assert errors.remove_rejected_parameter(error, kwargs, adapted, "model")
    kwargs.clear()
    assert errors.remove_rejected_parameter(error, kwargs, adapted, "model") is None
    assert kwargs == {"reasoning_effort": "none"}


def test_parameter_removal_remains_supported():
    kwargs = {"temperature": 0.2}
    assert errors.remove_rejected_parameter(
        rejection("unsupported parameter: temperature"), kwargs, set()
    ) == "temperature"
    assert kwargs == {}
