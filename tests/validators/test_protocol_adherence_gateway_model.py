"""protocol_adherence must send litellm's routing model, not the configured name.

FILE: tests/validators/test_protocol_adherence.py

Fixes #255.

For a gateway provider whose snodo config block name differs from its
litellm provider name (the normal case for a self-hosted or gateway
provider reached through the OpenAI-compatible driver), the completion
function binds the routing name and api_base.  protocol_adherence must not
override that binding with a ``model=`` kwarg: the raw configured name
("gateway/...") is not a provider litellm knows, and it would arrive
without the api_base.
"""

from unittest.mock import MagicMock, patch

import pytest
from snodo.compiler.models import Mode, Protocol, Validator
from snodo.config import ConfigManager, ProviderConfig
from snodo.core.interfaces import Task, ValidatorResult
from snodo.validators.context import ValidatorContext
from snodo.validators.protocol_adherence import ProtocolAdherenceValidator
from snodo.validators.runner import build_completion_fn

_GATEWAY_PROVIDERS = {
    "gateway": ProviderConfig(
        litellm_provider="openai",
        base_url="https://gateway.internal/v1",
        api_key="gateway-secret-key",
    )
}
_GATEWAY_MODEL = "gateway/deepseek-v4.1-flash"
_ROUTING_MODEL = "openai/deepseek-v4.1-flash"


@pytest.fixture
def gateway_provider(monkeypatch):
    """Register a gateway-style provider block whose name is not a litellm provider."""
    monkeypatch.setattr(ConfigManager, "get_providers", lambda self: _GATEWAY_PROVIDERS)


@pytest.fixture
def protocol():
    return Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit"],
                validators=["protocol_adherence"],
            )
        ],
        validators=[
            Validator(
                validator_id="protocol_adherence",
                validator_type="protocol",
                evaluation_phase="pre_execute",
            )
        ],
        initial_mode="producer",
    )


@pytest.fixture
def validator_spec():
    return Validator(
        validator_id="protocol_adherence",
        validator_type="protocol",
        evaluation_phase="pre_execute",
        criteria=["Examine the task spec against the current mode's profile."],
    )


def _context(protocol):
    return ValidatorContext(
        task=Task(id="t1", spec="Implement OAuth2 login flow"),
        current_mode=protocol.get_mode("producer"),
        protocol=protocol,
        mode_name="Producer",
        mode_tools=["edit"],
        mode_transitions={},
        mode_validator_refs=[],
    )


def test_protocol_adherence_sends_routing_model_not_configured_name(
    gateway_provider, protocol, validator_spec
):
    """The unstructured call reaches litellm with the routing model and api_base."""
    base_fn = MagicMock(
        return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(
                content='{"severity":"pass","justification":"ok"}'
            ))]
        )
    )
    completion_fn = build_completion_fn(_GATEWAY_MODEL, base_fn)
    validator = ProtocolAdherenceValidator(
        validator_spec, completion_fn, model=_GATEWAY_MODEL
    )

    with patch(
        "snodo.validators.protocol_adherence.supports_response_schema",
        return_value=False,
    ):
        result = validator.evaluate(_context(protocol))

    assert result.severity == "pass"
    call_kwargs = base_fn.call_args[1]
    assert call_kwargs["model"] == _ROUTING_MODEL
    assert call_kwargs["api_base"] == "https://gateway.internal/v1"
    assert call_kwargs["api_key"] == "gateway-secret-key"
    # The configured name must never be what litellm is asked to route.
    assert call_kwargs["model"] != _GATEWAY_MODEL


def test_protocol_adherence_structured_sends_routing_model_not_configured_name(
    gateway_provider, protocol, validator_spec
):
    """The structured call reaches litellm with the routing model and api_base."""
    vr = ValidatorResult(
        validator_id="protocol_adherence", severity="pass", justification="ok"
    )
    base_fn = MagicMock(
        return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content=vr.model_dump_json()))]
        )
    )
    completion_fn = build_completion_fn(_GATEWAY_MODEL, base_fn)
    validator = ProtocolAdherenceValidator(
        validator_spec, completion_fn, model=_GATEWAY_MODEL
    )

    with patch(
        "snodo.validators.protocol_adherence.supports_response_schema",
        return_value=True,
    ):
        result = validator.evaluate(_context(protocol))

    assert result.severity == "pass"
    call_kwargs = base_fn.call_args[1]
    assert call_kwargs["model"] == _ROUTING_MODEL
    assert call_kwargs["api_base"] == "https://gateway.internal/v1"
    assert call_kwargs["model"] != _GATEWAY_MODEL
