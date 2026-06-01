"""Comprehensive tests for protocol verifier.

Tests cover:
- Each WF rule individually
- Valid protocols that pass all checks
- Invalid protocols that fail with specific exceptions
- 100% code coverage
"""

import pytest

from snodo.compiler.models import (
    Protocol, Mode, Validator, Constraint, Role,
    DisagreementPolicy
)
from snodo.compiler.verifier import (
    ProtocolVerifier, verify_protocol, VerificationResult,
    ProtocolWellFormednessError,
    WF1Violation, WF2Violation, WF3Violation, WF4Violation, WF5Violation,
    WellFormednessViolation
)


# ========== HELPER FUNCTIONS ==========

def create_minimal_valid_protocol() -> Protocol:
    """Create a minimal valid protocol for testing."""
    return Protocol(
        protocol_id="test_proto",
        name="Test Protocol",
        modes=[Mode(mode_id="start", name="Start Mode")],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="start"
    )


# ========== VALID PROTOCOL TESTS ==========

def test_minimal_valid_protocol_passes():
    """Test that a minimal valid protocol passes all checks."""
    protocol = create_minimal_valid_protocol()
    result = verify_protocol(protocol)
    
    assert result.passed
    assert len(result.errors) == 0
    assert bool(result) is True  # Test __bool__ method


def test_full_valid_protocol_passes():
    """Test that a complete valid protocol passes all checks."""
    protocol = Protocol(
        protocol_id="full_proto",
        name="Full Protocol",
        modes=[
            Mode(
                mode_id="plan",
                name="Planning",
                tools=["tool1", "tool2"],
                validators=["v1"],
                constraints=[Constraint(
                    constraint_id="c1",
                    description="Plan constraint",
                    expression="has_plan()"
                )]
            ),
            Mode(
                mode_id="impl",
                name="Implementation",
                tools=["tool3", "tool4"],
                validators=["v1", "v2"]
            )
        ],
        roles=[
            Role(role_id="r1", name="Role 1"),
            Role(role_id="r2", name="Role 2")
        ],
        validators=[
            Validator(validator_id="v1", validator_type="security"),
            Validator(
                validator_id="v2",
                validator_type="architecture",
                constraints=[Constraint(
                    constraint_id="c2",
                    description="Arch constraint",
                    expression="is_modular()"
                )]
            )
        ],
        initial_mode="plan",
        disagreement_policy=DisagreementPolicy.MAJORITY,
        global_constraints=[Constraint(
            constraint_id="gc1",
            description="Global constraint",
            expression="valid()"
        )]
    )
    
    result = verify_protocol(protocol)
    assert result.passed
    assert len(result.errors) == 0


def test_verifier_class_interface():
    """Test ProtocolVerifier class can be used directly."""
    protocol = create_minimal_valid_protocol()
    verifier = ProtocolVerifier(protocol)
    result = verifier.verify()
    
    assert result.passed
    assert isinstance(result, VerificationResult)


# ========== WF1: MODE SEPARATION TESTS ==========

def test_wf1_disjoint_tool_sets_pass():
    """Test that modes with disjoint tool sets pass WF1."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(mode_id="m1", name="Mode 1", tools=["a", "b"]),
            Mode(mode_id="m2", name="Mode 2", tools=["c", "d"]),
            Mode(mode_id="m3", name="Mode 3", tools=["e"])
        ],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="m1"
    )
    
    verifier = ProtocolVerifier(protocol)
    verifier.check_wf1()  # Should not raise


def test_wf1_overlapping_tools_fail():
    """Test that modes with overlapping tools fail WF1."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(mode_id="m1", name="Mode 1", tools=["a", "b", "c"]),
            Mode(mode_id="m2", name="Mode 2", tools=["c", "d"])  # 'c' overlaps
        ],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="m1"
    )
    
    verifier = ProtocolVerifier(protocol)
    with pytest.raises(WF1Violation, match="share tools"):
        verifier.check_wf1()


def test_wf1_empty_tool_sets_pass():
    """Test that modes with empty tool sets pass WF1."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(mode_id="m1", name="Mode 1", tools=[]),
            Mode(mode_id="m2", name="Mode 2", tools=[])
        ],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="m1"
    )
    
    verifier = ProtocolVerifier(protocol)
    verifier.check_wf1()  # Should not raise


# ========== WF2: ROLE UNIQUENESS TESTS ==========

def test_wf2_unique_roles_pass():
    """Test that unique role IDs pass WF2."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[Mode(mode_id="m1", name="Mode")],
        roles=[
            Role(role_id="r1", name="Role 1"),
            Role(role_id="r2", name="Role 2"),
            Role(role_id="r3", name="Role 3")
        ],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="m1"
    )
    
    verifier = ProtocolVerifier(protocol)
    verifier.check_wf2()  # Should not raise


def test_wf2_duplicate_roles_fail():
    """Test that duplicate role IDs fail WF2."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[Mode(mode_id="m1", name="Mode")],
        roles=[
            Role(role_id="r1", name="Role 1"),
            Role(role_id="r1", name="Role 1 Duplicate"),  # Duplicate
            Role(role_id="r2", name="Role 2")
        ],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="m1"
    )
    
    verifier = ProtocolVerifier(protocol)
    with pytest.raises(WF2Violation, match="Duplicate role IDs"):
        verifier.check_wf2()


def test_wf2_no_roles_pass():
    """Test that protocols with no roles pass WF2."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[Mode(mode_id="m1", name="Mode")],
        roles=[],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="m1"
    )
    
    verifier = ProtocolVerifier(protocol)
    verifier.check_wf2()  # Should not raise


# ========== WF3: VALIDATOR COVERAGE TESTS ==========

def test_wf3_all_validators_defined_pass():
    """Test that all referenced validators exist passes WF3."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(mode_id="m1", name="Mode 1", validators=["v1", "v2"]),
            Mode(mode_id="m2", name="Mode 2", validators=["v1"])
        ],
        validators=[
            Validator(validator_id="v1", validator_type="security"),
            Validator(validator_id="v2", validator_type="architecture")
        ],
        initial_mode="m1"
    )
    
    verifier = ProtocolVerifier(protocol)
    verifier.check_wf3()  # Should not raise


def test_wf3_undefined_validator_fails():
    """Test that undefined validator reference fails WF3."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(mode_id="m1", name="Mode", validators=["v1", "v_undefined"])
        ],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="m1"
    )
    
    verifier = ProtocolVerifier(protocol)
    with pytest.raises(WF3Violation, match="undefined validator"):
        verifier.check_wf3()


def test_wf3_invalid_initial_mode_fails():
    """Test that invalid initial mode fails WF3."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[Mode(mode_id="m1", name="Mode")],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="nonexistent"
    )
    
    verifier = ProtocolVerifier(protocol)
    with pytest.raises(WF3Violation, match="Initial mode.*not defined"):
        verifier.check_wf3()


# ========== WF4: POLICY COMPLETENESS TESTS ==========

def test_wf4_unanimous_with_one_validator_passes():
    """Test UNANIMOUS policy with 1+ validators passes WF4."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[Mode(mode_id="m1", name="Mode")],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="m1",
        disagreement_policy=DisagreementPolicy.UNANIMOUS
    )
    
    verifier = ProtocolVerifier(protocol)
    verifier.check_wf4()  # Should not raise


def test_wf4_majority_with_two_validators_passes():
    """Test MAJORITY policy with 2+ validators passes WF4."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[Mode(mode_id="m1", name="Mode")],
        validators=[
            Validator(validator_id="v1", validator_type="security"),
            Validator(validator_id="v2", validator_type="architecture")
        ],
        initial_mode="m1",
        disagreement_policy=DisagreementPolicy.MAJORITY
    )
    
    verifier = ProtocolVerifier(protocol)
    verifier.check_wf4()  # Should not raise


def test_wf4_majority_with_one_validator_fails():
    """Test MAJORITY policy with <2 validators fails WF4."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[Mode(mode_id="m1", name="Mode")],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="m1",
        disagreement_policy=DisagreementPolicy.MAJORITY
    )
    
    verifier = ProtocolVerifier(protocol)
    with pytest.raises(WF4Violation, match="MAJORITY policy requires at least 2"):
        verifier.check_wf4()


def test_wf4_quorum_with_few_validators_warns():
    """Test QUORUM policy with <3 validators generates warning."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[Mode(mode_id="m1", name="Mode")],
        validators=[
            Validator(validator_id="v1", validator_type="security"),
            Validator(validator_id="v2", validator_type="architecture")
        ],
        initial_mode="m1",
        disagreement_policy=DisagreementPolicy.QUORUM
    )
    
    verifier = ProtocolVerifier(protocol)
    verifier.check_wf4()
    assert len(verifier.warnings) > 0
    assert "QUORUM" in verifier.warnings[0]


# ========== WF5: CONSTRAINT CONSISTENCY TESTS ==========

def test_wf5_unique_constraints_pass():
    """Test that unique constraint IDs pass WF5."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(
                mode_id="m1",
                name="Mode",
                constraints=[Constraint(
                    constraint_id="c1",
                    description="Test",
                    expression="valid()"
                )]
            )
        ],
        validators=[Validator(
            validator_id="v1",
            validator_type="security",
            constraints=[Constraint(
                constraint_id="c2",
                description="Test",
                expression="secure()"
            )]
        )],
        initial_mode="m1",
        global_constraints=[Constraint(
            constraint_id="c3",
            description="Test",
            expression="global()"
        )]
    )
    
    verifier = ProtocolVerifier(protocol)
    verifier.check_wf5()  # Should not raise


def test_wf5_duplicate_constraint_ids_fail():
    """Test that duplicate constraint IDs fail WF5."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(
                mode_id="m1",
                name="Mode",
                constraints=[Constraint(
                    constraint_id="c1",
                    description="Test",
                    expression="valid()"
                )]
            )
        ],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="m1",
        global_constraints=[Constraint(
            constraint_id="c1",  # Duplicate
            description="Test",
            expression="global()"
        )]
    )
    
    verifier = ProtocolVerifier(protocol)
    with pytest.raises(WF5Violation, match="Duplicate constraint IDs"):
        verifier.check_wf5()


def test_wf5_empty_expression_fails():
    """Test that empty constraint expression fails WF5."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[Mode(mode_id="m1", name="Mode")],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="m1",
        global_constraints=[Constraint(
            constraint_id="c1",
            description="Test",
            expression=""  # Empty expression
        )]
    )
    
    verifier = ProtocolVerifier(protocol)
    with pytest.raises(WF5Violation, match="empty expression"):
        verifier.check_wf5()


def test_wf5_whitespace_expression_fails():
    """Test that whitespace-only expression fails WF5."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[Mode(mode_id="m1", name="Mode")],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="m1",
        global_constraints=[Constraint(
            constraint_id="c1",
            description="Test",
            expression="   "  # Whitespace only
        )]
    )
    
    verifier = ProtocolVerifier(protocol)
    with pytest.raises(WF5Violation, match="empty expression"):
        verifier.check_wf5()


# ========== INTEGRATION TESTS ==========

def test_verify_catches_multiple_violations():
    """Test that verify() catches all violations in sequence."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(mode_id="m1", name="Mode 1", tools=["a"]),
            Mode(mode_id="m2", name="Mode 2", tools=["a"])  # WF1 violation
        ],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="m1"
    )
    
    result = verify_protocol(protocol)
    assert not result.passed
    assert len(result.errors) > 0
    assert "WF1" in result.errors[0]


def test_verification_result_bool_conversion():
    """Test VerificationResult __bool__ method."""
    # Passing result
    result = VerificationResult(passed=True, errors=[], warnings=[])
    assert bool(result) is True
    
    # Failing result
    result = VerificationResult(passed=False, errors=["error"], warnings=[])
    assert bool(result) is False


def test_verify_stops_on_first_error():
    """Test that verify() stops on first violation."""
    # Create protocol with multiple violations
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(mode_id="m1", name="Mode 1", tools=["a"]),
            Mode(mode_id="m2", name="Mode 2", tools=["a"])  # WF1
        ],
        roles=[
            Role(role_id="r1", name="Role"),
            Role(role_id="r1", name="Role")  # WF2
        ],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="nonexistent"  # WF3
    )
    
    result = verify_protocol(protocol)
    assert not result.passed
    # Should catch first violation and stop
    assert len(result.errors) >= 1


def test_all_wf_violation_exceptions_exist():
    """Test that all WF violation exception classes exist."""
    assert issubclass(WF1Violation, WellFormednessViolation)
    assert issubclass(WF2Violation, WellFormednessViolation)
    assert issubclass(WF3Violation, WellFormednessViolation)
    assert issubclass(WF4Violation, WellFormednessViolation)
    assert issubclass(WF5Violation, WellFormednessViolation)


# ========== PROTOCOL WELL-FORMEDNESS ERROR ==========

def test_protocol_wellformedness_error():
    """Test ProtocolWellFormednessError formatting."""
    err = ProtocolWellFormednessError(["WF1: overlap", "WF3: missing coverage"])
    assert err.violations == ["WF1: overlap", "WF3: missing coverage"]
    assert "WF1: overlap" in str(err)
    assert "WF3: missing coverage" in str(err)


def test_protocol_wellformedness_error_single():
    """Test ProtocolWellFormednessError with a single violation."""
    err = ProtocolWellFormednessError(["WF3 Violation: bad"])
    assert len(err.violations) == 1
    assert "well-formedness" in str(err).lower()


# ========== WF3: PHASE COVERAGE TESTS (DISPATCH) ==========

def test_wf3_dispatch_mode_with_pre_execute_passes():
    """Dispatching mode with pre_execute validator passes WF3."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit", "dispatch"],
                validators=["v1"],
            )
        ],
        validators=[
            Validator(
                validator_id="v1",
                validator_type="security",
                evaluation_phase="pre_execute",
            )
        ],
        initial_mode="producer",
    )
    verifier = ProtocolVerifier(protocol)
    verifier.check_wf3()  # Should not raise


def test_wf3_dispatch_mode_with_only_post_execute_fails():
    """Dispatching mode with only post_execute validator fails WF3."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit", "dispatch"],
                validators=["v1"],
            )
        ],
        validators=[
            Validator(
                validator_id="v1",
                validator_type="quality",
                evaluation_phase="post_execute",
            )
        ],
        initial_mode="producer",
    )
    verifier = ProtocolVerifier(protocol)
    with pytest.raises(WF3Violation, match="no pre_execute validators"):
        verifier.check_wf3()


def test_wf3_dispatch_mode_with_empty_validators_fails():
    """Dispatching mode with no validators at all fails WF3."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit", "dispatch"],
                validators=[],
            )
        ],
        validators=[
            Validator(validator_id="v1", validator_type="security")
        ],
        initial_mode="producer",
    )
    verifier = ProtocolVerifier(protocol)
    with pytest.raises(WF3Violation, match="no pre_execute validators"):
        verifier.check_wf3()


def test_wf3_non_dispatch_mode_without_validators_passes():
    """Non-dispatching mode without validators passes WF3."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(
                mode_id="reviewer",
                name="Reviewer",
                tools=["review", "approve"],
                validators=[],
            )
        ],
        validators=[
            Validator(validator_id="v1", validator_type="security")
        ],
        initial_mode="reviewer",
    )
    verifier = ProtocolVerifier(protocol)
    verifier.check_wf3()  # Should not raise


def test_wf3_dispatch_with_both_phases_passes():
    """Dispatching mode with both pre and post validators passes WF3."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit", "dispatch"],
                validators=["v1", "v2"],
            )
        ],
        validators=[
            Validator(
                validator_id="v1",
                validator_type="security",
                evaluation_phase="pre_execute",
            ),
            Validator(
                validator_id="v2",
                validator_type="quality",
                evaluation_phase="post_execute",
            ),
        ],
        initial_mode="producer",
    )
    verifier = ProtocolVerifier(protocol)
    verifier.check_wf3()  # Should not raise


def test_wf3_multiple_dispatch_modes_one_uncovered():
    """Two dispatch modes, one uncovered — fails WF3."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(
                mode_id="m1",
                name="Mode 1",
                tools=["dispatch"],
                validators=["v1"],
            ),
            Mode(
                mode_id="m2",
                name="Mode 2",
                tools=["dispatch", "other"],
                validators=["v2"],
            ),
        ],
        validators=[
            Validator(
                validator_id="v1",
                validator_type="security",
                evaluation_phase="pre_execute",
            ),
            Validator(
                validator_id="v2",
                validator_type="quality",
                evaluation_phase="post_execute",
            ),
        ],
        initial_mode="m1",
    )
    verifier = ProtocolVerifier(protocol)
    with pytest.raises(WF3Violation, match="m2"):
        verifier.check_wf3()


def test_wf3_via_verify_protocol_integration():
    """verify_protocol catches WF3 phase coverage failure."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit", "dispatch"],
                validators=["v1"],
            )
        ],
        validators=[
            Validator(
                validator_id="v1",
                validator_type="quality",
                evaluation_phase="post_execute",
            )
        ],
        initial_mode="producer",
    )
    result = verify_protocol(protocol)
    assert not result.passed
    assert any("no pre_execute" in e for e in result.errors)

