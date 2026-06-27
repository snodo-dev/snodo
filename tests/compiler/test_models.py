"""Comprehensive tests for protocol syntax models.

Tests cover:
- Valid protocol examples
- Invalid protocol examples (validation failures)
- Edge cases
- 100% code coverage
"""

import pytest
from pydantic import ValidationError

from snodo.compiler.models import (
    Protocol, Mode, Role, Validator, Constraint,
    DisagreementPolicy, Severity, EVALUATION_PHASES
)


# ========== VALID PROTOCOL EXAMPLES ==========

def test_minimal_valid_protocol():
    """Test 1: Minimal valid protocol with required fields only."""
    protocol = Protocol(
        protocol_id="minimal_proto",
        name="Minimal Protocol",
        modes=[Mode(
            mode_id="start",
            name="Start Mode"
        )],
        validators=[Validator(
            validator_id="val1",
            validator_type="security"
        )],
        initial_mode="start"
    )
    assert protocol.protocol_id == "minimal_proto"
    assert len(protocol.modes) == 1
    assert len(protocol.validators) == 1
    assert protocol.disagreement_policy == DisagreementPolicy.UNANIMOUS


def test_full_featured_protocol():
    """Test 2: Full protocol with all features."""
    protocol = Protocol(
        protocol_id="full_proto",
        name="Full Featured Protocol",
        version="2.0.0",
        modes=[
            Mode(
                mode_id="plan",
                name="Planning",
                tools=["tool1", "tool2"],
                transitions={"next": "impl"},
                validators=["val1"],
                constraints=[Constraint(
                    constraint_id="c1",
                    description="Test constraint",
                    expression="true"
                )]
            ),
            Mode(
                mode_id="impl",
                name="Implementation",
                tools=["tool3"]
            )
        ],
        roles=[Role(
            role_id="dev",
            name="Developer",
            permissions=["code", "test"],
            responsibilities=["implement", "test"]
        )],
        validators=[
            Validator(
                validator_id="val1",
                validator_type="architecture",
                criteria=["modularity", "separation"],
                constraints=[Constraint(
                    constraint_id="c2",
                    description="Arch constraint",
                    expression="is_modular()"
                )]
            )
        ],
        disagreement_policy=DisagreementPolicy.MAJORITY,
        initial_mode="plan",
        global_constraints=[Constraint(
            constraint_id="gc1",
            description="Global constraint",
            expression="complexity() < 10",
            severity=Severity.WARN
        )],
        metadata={"author": "test"}
    )
    assert protocol.version == "2.0.0"
    assert protocol.disagreement_policy == DisagreementPolicy.MAJORITY
    assert len(protocol.global_constraints) == 1


def test_multi_mode_workflow():
    """Test 3: Protocol with complex mode transitions."""
    protocol = Protocol(
        protocol_id="workflow",
        name="Workflow Protocol",
        modes=[
            Mode(mode_id="draft", name="Draft", transitions={"submit": "review"}),
            Mode(mode_id="review", name="Review", transitions={"approve": "done", "reject": "draft"}),
            Mode(mode_id="done", name="Done")
        ],
        validators=[Validator(validator_id="v1", validator_type="conventions")],
        initial_mode="draft"
    )
    assert len(protocol.modes) == 3
    draft_mode = protocol.get_mode("draft")
    assert draft_mode is not None
    assert draft_mode.transitions["submit"] == "review"


def test_all_disagreement_policies():
    """Test 4: Protocol with each disagreement policy."""
    for policy in DisagreementPolicy:
        protocol = Protocol(
            protocol_id=f"proto_{policy.value}",
            name=f"Protocol {policy.value}",
            modes=[Mode(mode_id="m1", name="Mode")],
            validators=[Validator(validator_id="v1", validator_type="security")],
            initial_mode="m1",
            disagreement_policy=policy
        )
        assert protocol.disagreement_policy == policy


def test_multiple_validators_and_constraints():
    """Test 5: Protocol with multiple validators and constraints."""
    protocol = Protocol(
        protocol_id="multi_val",
        name="Multi Validator",
        modes=[Mode(mode_id="m1", name="Mode", validators=["v1", "v2", "v3"])],
        validators=[
            Validator(validator_id="v1", validator_type="security"),
            Validator(validator_id="v2", validator_type="architecture"),
            Validator(validator_id="v3", validator_type="conventions")
        ],
        initial_mode="m1"
    )
    assert len(protocol.validators) == 3
    assert all(v.validator_id in ["v1", "v2", "v3"] for v in protocol.validators)


# ========== INVALID PROTOCOL EXAMPLES ==========

def test_empty_protocol_id():
    """Test 6: Protocol with empty ID (invalid)."""
    with pytest.raises(ValidationError, match="protocol_id cannot be empty"):
        Protocol(
            protocol_id="",
            name="Invalid",
            modes=[Mode(mode_id="m1", name="Mode")],
            validators=[Validator(validator_id="v1", validator_type="security")],
            initial_mode="m1"
        )


def test_no_modes():
    """Test 7: Protocol with no modes (invalid)."""
    with pytest.raises(ValidationError):
        Protocol(
            protocol_id="no_modes",
            name="No Modes",
            modes=[],  # Empty list violates min_length=1
            validators=[Validator(validator_id="v1", validator_type="security")],
            initial_mode="m1"
        )


def test_no_validators():
    """Test 8: Protocol with no validators (invalid)."""
    with pytest.raises(ValidationError):
        Protocol(
            protocol_id="no_validators",
            name="No Validators",
            modes=[Mode(mode_id="m1", name="Mode")],
            validators=[],  # Empty list violates min_length=1
            initial_mode="m1"
        )


def test_duplicate_mode_ids():
    """Test 9: Protocol with duplicate mode IDs (invalid)."""
    with pytest.raises(ValidationError, match="mode IDs must be unique"):
        Protocol(
            protocol_id="dup_modes",
            name="Duplicate Modes",
            modes=[
                Mode(mode_id="same", name="Mode 1"),
                Mode(mode_id="same", name="Mode 2")
            ],
            validators=[Validator(validator_id="v1", validator_type="security")],
            initial_mode="same"
        )


def test_duplicate_validator_ids():
    """Test 10: Protocol with duplicate validator IDs (invalid)."""
    with pytest.raises(ValidationError, match="validator IDs must be unique"):
        Protocol(
            protocol_id="dup_vals",
            name="Duplicate Validators",
            modes=[Mode(mode_id="m1", name="Mode")],
            validators=[
                Validator(validator_id="same", validator_type="security"),
                Validator(validator_id="same", validator_type="architecture")
            ],
            initial_mode="m1"
        )


# ========== COMPONENT TESTS ==========

def test_mode_empty_id():
    """Test mode with empty ID."""
    with pytest.raises(ValidationError, match="mode_id cannot be empty"):
        Mode(mode_id="", name="Invalid Mode")


def test_mode_invalid_transitions():
    """Test mode with empty transition event/target."""
    with pytest.raises(ValidationError, match="transitions must have non-empty"):
        Mode(
            mode_id="m1",
            name="Mode",
            transitions={"": "target"}
        )


def test_validator_accepts_any_type():
    """Validator type is now open (7.20) — any string accepted."""
    v = Validator(
        validator_id="v1",
        validator_type="custom_my_checker"
    )
    assert v.validator_type == "custom_my_checker"


def test_constraint_invalid_id():
    """Test constraint with invalid ID."""
    with pytest.raises(ValidationError, match="constraint_id must be alphanumeric"):
        Constraint(
            constraint_id="invalid@id!",
            description="Test",
            expression="true"
        )


def test_role_empty_id():
    """Test role with empty ID."""
    with pytest.raises(ValidationError, match="role_id cannot be empty"):
        Role(role_id="", name="Invalid Role")


# ========== UTILITY METHOD TESTS ==========

def test_get_mode():
    """Test Protocol.get_mode() method."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(mode_id="m1", name="Mode 1"),
            Mode(mode_id="m2", name="Mode 2")
        ],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="m1"
    )
    assert protocol.get_mode("m1").name == "Mode 1"
    assert protocol.get_mode("m2").name == "Mode 2"
    assert protocol.get_mode("nonexistent") is None


def test_get_validator():
    """Test Protocol.get_validator() method."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[Mode(mode_id="m1", name="Mode")],
        validators=[
            Validator(validator_id="v1", validator_type="security"),
            Validator(validator_id="v2", validator_type="architecture")
        ],
        initial_mode="m1"
    )
    assert protocol.get_validator("v1").validator_type == "security"
    assert protocol.get_validator("v2").validator_type == "architecture"
    assert protocol.get_validator("nonexistent") is None


def test_get_role():
    """Test Protocol.get_role() method."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[Mode(mode_id="m1", name="Mode")],
        roles=[Role(role_id="r1", name="Role 1")],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="m1"
    )
    assert protocol.get_role("r1").name == "Role 1"
    assert protocol.get_role("nonexistent") is None


# ========== IMMUTABILITY TESTS ==========

def test_protocol_immutability():
    """Test that Protocol instances are frozen."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[Mode(mode_id="m1", name="Mode")],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="m1"
    )
    with pytest.raises(ValidationError):
        protocol.name = "Modified"


def test_mode_immutability():
    """Test that Mode instances are frozen."""
    mode = Mode(mode_id="m1", name="Mode")
    with pytest.raises(ValidationError):
        mode.name = "Modified"


def test_validator_immutability():
    """Test that Validator instances are frozen."""
    validator = Validator(validator_id="v1", validator_type="security")
    with pytest.raises(ValidationError):
        validator.validator_type = "architecture"


# ========== YAML INTEGRATION TEST ==========

def test_yaml_roundtrip():
    """Test loading and dumping protocol from/to YAML."""
    protocol_dict = {
        "protocol_id": "yaml_test",
        "name": "YAML Test",
        "modes": [{"mode_id": "m1", "name": "Mode"}],
        "validators": [{"validator_id": "v1", "validator_type": "security"}],
        "initial_mode": "m1"
    }
    
    protocol = Protocol(**protocol_dict)
    assert protocol.protocol_id == "yaml_test"
    
    # Convert back to dict
    output_dict = protocol.model_dump()
    assert output_dict["protocol_id"] == "yaml_test"
    assert output_dict["disagreement_policy"] == "unanimous"


# ========== COVERAGE COMPLETENESS TESTS ==========

def test_all_severity_levels():
    """Test all Severity enum values."""
    assert Severity.PASS == "pass"
    assert Severity.WARN == "warn"
    assert Severity.BLOCKER == "blocker"


def test_disagreement_policy_enum_values():
    """Test all DisagreementPolicy enum values."""
    assert DisagreementPolicy.UNANIMOUS == "unanimous"
    assert DisagreementPolicy.MAJORITY == "majority"
    assert DisagreementPolicy.QUORUM == "quorum"
    assert DisagreementPolicy.ANY == "any"


def test_constraint_default_severity():
    """Test Constraint with default severity."""
    c = Constraint(
        constraint_id="c1",
        description="Test",
        expression="true"
    )
    assert c.severity == Severity.BLOCKER


def test_validator_default_severity_cap():
    """Test Validator with default severity cap (None = no cap)."""
    v = Validator(
        validator_id="v1",
        validator_type="architecture"
    )
    assert v.severity_cap is None


def test_protocol_default_version():
    """Test Protocol with default version."""
    p = Protocol(
        protocol_id="test",
        name="Test",
        modes=[Mode(mode_id="m1", name="Mode")],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="m1"
    )
    assert p.version == "1.0.0"


# ========== EVALUATION PHASE ENUM TESTS ==========

def test_evaluation_phases_constant():
    """EVALUATION_PHASES contains expected phases."""
    assert EVALUATION_PHASES == {"pre_execute", "post_execute", "mode_transition"}


def test_valid_evaluation_phases():
    """All valid phases are accepted."""
    for phase in EVALUATION_PHASES:
        v = Validator(validator_id="v1", validator_type="security", evaluation_phase=phase)
        assert v.evaluation_phase == phase


def test_invalid_evaluation_phase_rejected():
    """Invalid evaluation_phase raises ValidationError."""
    with pytest.raises(ValidationError, match="evaluation_phase must be one of"):
        Validator(validator_id="v1", validator_type="security", evaluation_phase="mid_execute")


def test_empty_evaluation_phase_rejected():
    """Empty evaluation_phase raises ValidationError."""
    with pytest.raises(ValidationError, match="evaluation_phase must be one of"):
        Validator(validator_id="v1", validator_type="security", evaluation_phase="")


def test_default_evaluation_phase():
    """Default evaluation_phase is pre_execute."""
    v = Validator(validator_id="v1", validator_type="security")
    assert v.evaluation_phase == "pre_execute"