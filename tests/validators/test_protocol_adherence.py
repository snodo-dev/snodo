"""Tests for protocol-adherence validator (Task 7.11).

FILE: tests/validators/test_protocol_adherence.py
"""

from unittest.mock import Mock

import pytest

from snodo.compiler.models import (
    Protocol, Mode, Validator, DisagreementPolicy
)
from snodo.core.interfaces import Task
from snodo.validators.context import ValidatorContext
from snodo.validators.protocol_adherence import ProtocolAdherenceValidator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def protocol():
    return Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(
                mode_id="producer", name="Producer Mode",
                tools=["edit", "dispatch", "test"],
                validators=["sec", "arch"],
                transitions={"complete": "reviewer"},
            ),
            Mode(
                mode_id="reviewer", name="Reviewer Mode",
                tools=["review", "approve", "merge"],
                validators=["sec"],
                transitions={"approved": "complete"},
            ),
        ],
        validators=[
            Validator(validator_id="sec", validator_type="security",
                       evaluation_phase="pre_execute"),
            Validator(validator_id="arch", validator_type="architecture",
                       evaluation_phase="pre_execute"),
        ],
        initial_mode="producer",
    )


@pytest.fixture
def producer_mode(protocol):
    return protocol.get_mode("producer")


@pytest.fixture
def validator_spec():
    return Validator(
        validator_id="protocol_adherence",
        validator_type="protocol",
        evaluation_phase="pre_execute",
        criteria=[
            "Examine the task spec against the current mode's profile.",
            "Flag work that belongs to sibling modes.",
        ],
    )


@pytest.fixture
def task():
    return Task(id="t1", spec="Implement OAuth2 login flow")


# ---------------------------------------------------------------------------
# ValidatorContext tests
# ---------------------------------------------------------------------------

def test_validator_context_construction(task, producer_mode, protocol):
    ctx = ValidatorContext(
        task=task,
        current_mode=producer_mode,
        protocol=protocol,
        mode_name=producer_mode.name,
        mode_tools=list(producer_mode.tools),
        mode_transitions=dict(producer_mode.transitions),
        mode_validator_refs=list(producer_mode.validators),
    )
    assert ctx.task is task
    assert ctx.current_mode is producer_mode
    assert ctx.mode_name == "Producer Mode"
    assert ctx.mode_tools == ["edit", "dispatch", "test"]


# ---------------------------------------------------------------------------
# Mode profile derivation
# ---------------------------------------------------------------------------

def test_derive_mode_profile(producer_mode):
    profile = ProtocolAdherenceValidator._derive_mode_profile(producer_mode)
    assert profile["mode_id"] == "producer"
    assert profile["mode_name"] == "Producer Mode"
    assert profile["tools"] == ["edit", "dispatch", "test"]
    assert profile["transitions"] == {"complete": "reviewer"}


def test_enrich_profile_adds_validator_details(protocol, producer_mode):
    """_enrich_profile resolves validator IDs to (id, type) pairs."""
    profile = ProtocolAdherenceValidator._derive_mode_profile(producer_mode)
    validator = ProtocolAdherenceValidator(
        validator_spec=Validator(
            validator_id="x", validator_type="protocol",
            evaluation_phase="pre_execute",
        ),
        completion_fn=Mock(),
    )
    enriched = validator._enrich_profile(profile, protocol)
    assert len(enriched["applied_validators"]) == 2
    ids = {v["validator_id"] for v in enriched["applied_validators"]}
    assert ids == {"sec", "arch"}


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def test_build_prompt_includes_mode_profiles(task, producer_mode, protocol, validator_spec):
    mock_llm = Mock()
    mock_llm.return_value = Mock(
        choices=[Mock(message=Mock(content='{"severity":"pass","justification":"ok"}'))]
    )
    val = ProtocolAdherenceValidator(validator_spec, mock_llm)

    ctx = ValidatorContext(
        task=task, current_mode=producer_mode, protocol=protocol,
        mode_name=producer_mode.name,
        mode_tools=list(producer_mode.tools),
        mode_transitions=dict(producer_mode.transitions),
        mode_validator_refs=list(producer_mode.validators),
    )

    # _build_prompt is called internally by evaluate; verify it succeeds
    prompt = val._build_prompt(ctx)
    assert "Producer Mode" in prompt
    assert "Reviewer Mode" in prompt
    assert "edit" in prompt
    assert task.spec in prompt


# ---------------------------------------------------------------------------
# LLM response parsing
# ---------------------------------------------------------------------------

def test_parse_pass_result(validator_spec):
    val = ProtocolAdherenceValidator(validator_spec, Mock())
    result = val._parse_response('{"severity":"pass","justification":"all good"}')
    assert result.severity == "pass"
    assert result.justification == "all good"


def test_parse_blocker_result(validator_spec):
    val = ProtocolAdherenceValidator(validator_spec, Mock())
    result = val._parse_response('{"severity":"blocker","justification":"planning work in producer"}')
    assert result.severity == "blocker"


def test_parse_invalid_severity(validator_spec):
    val = ProtocolAdherenceValidator(validator_spec, Mock())
    result = val._parse_response('{"severity":"critical","justification":"..."}')
    assert result.severity == "warn"
    assert "Invalid severity" in result.justification


def test_parse_malformed_json(validator_spec):
    val = ProtocolAdherenceValidator(validator_spec, Mock())
    result = val._parse_response("not json at all")
    assert result.severity == "warn"


def test_evaluate_passes(task, producer_mode, protocol, validator_spec):
    mock_llm = Mock()
    mock_llm.return_value = Mock(
        choices=[Mock(message=Mock(content='{"severity":"pass","justification":"work aligns with producer mode"}'))]
    )
    val = ProtocolAdherenceValidator(validator_spec, mock_llm)
    ctx = ValidatorContext(
        task=task, current_mode=producer_mode, protocol=protocol,
        mode_name=producer_mode.name,
        mode_tools=list(producer_mode.tools),
        mode_transitions=dict(producer_mode.transitions),
        mode_validator_refs=list(producer_mode.validators),
    )
    result = val.evaluate(ctx)
    assert result.severity == "pass"


def test_evaluate_warns(task, producer_mode, protocol, validator_spec):
    mock_llm = Mock()
    mock_llm.return_value = Mock(
        choices=[Mock(message=Mock(content='{"severity":"warn","justification":"planning work detected"}'))]
    )
    val = ProtocolAdherenceValidator(validator_spec, mock_llm)
    ctx = ValidatorContext(
        task=task, current_mode=producer_mode, protocol=protocol,
        mode_name=producer_mode.name,
        mode_tools=list(producer_mode.tools),
        mode_transitions=dict(producer_mode.transitions),
        mode_validator_refs=list(producer_mode.validators),
    )
    result = val.evaluate(ctx)
    assert result.severity == "warn"


def test_evaluate_llm_failure_falls_back_to_warn(task, producer_mode, protocol, validator_spec):
    mock_llm = Mock(side_effect=Exception("API unavailable"))
    val = ProtocolAdherenceValidator(validator_spec, mock_llm)
    ctx = ValidatorContext(
        task=task, current_mode=producer_mode, protocol=protocol,
        mode_name=producer_mode.name,
        mode_tools=list(producer_mode.tools),
        mode_transitions=dict(producer_mode.transitions),
        mode_validator_refs=list(producer_mode.validators),
    )
    result = val.evaluate(ctx)
    assert result.severity == "warn"
    assert "defaulting to warn" in result.justification.lower()


# ---------------------------------------------------------------------------
# Single-mode protocol (solo-like)
# ---------------------------------------------------------------------------

def test_single_mode_no_sibling_profiles(task, validator_spec):
    """Single-mode protocol produces no sibling profiles section."""
    solo_protocol = Protocol(
        protocol_id="solo",
        name="Solo",
        modes=[Mode(mode_id="producer", name="Producer", tools=["edit"],
                      validators=[], transitions={})],
        validators=[Validator(validator_id="x", validator_type="security",
                               evaluation_phase="pre_execute")],
        initial_mode="producer",
    )
    ctx = ValidatorContext(
        task=task,
        current_mode=solo_protocol.get_mode("producer"),
        protocol=solo_protocol,
        mode_name="Producer",
        mode_tools=["edit"],
        mode_transitions={},
        mode_validator_refs=[],
    )
    val = ProtocolAdherenceValidator(validator_spec, Mock())
    prompt = val._build_prompt(ctx)
    assert "Sibling Modes" not in prompt
