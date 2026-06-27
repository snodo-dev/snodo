"""Well-formedness checkers for protocol verification.

Implements static checks based on Section 4.4 Well-Formedness Conditions.
"""

from typing import List, Set, Dict
from dataclasses import dataclass

from snodo.compiler.models import Protocol, Constraint
import snodo.predicates.scope  # noqa: F401 — ensures predicates self-register
import snodo.predicates.tests  # noqa: F401
import snodo.predicates.secrets  # noqa: F401
from snodo.predicates.registry import _default_registry


class WellFormednessViolation(Exception):
    """Base exception for well-formedness violations."""


class WF1Violation(WellFormednessViolation):
    """WF1: Mode separation violation - tool sets not disjoint."""


class WF2Violation(WellFormednessViolation):
    """WF2: Role uniqueness violation - duplicate roles in mode."""


class WF3Violation(WellFormednessViolation):
    """WF3: Validator coverage violation - undefined validator referenced."""


class WF4Violation(WellFormednessViolation):
    """WF4: Policy completeness violation - invalid disagreement policy."""


class WF5Violation(WellFormednessViolation):
    """WF5: Constraint consistency violation - invalid or conflicting constraints."""


class ProtocolWellFormednessError(Exception):
    """Raised when a protocol fails well-formedness verification at load time."""

    def __init__(self, violations: List[str]):
        self.violations = violations
        super().__init__(
            "Protocol violates well-formedness conditions:\n  - "
            + "\n  - ".join(violations)
        )


@dataclass
class VerificationResult:
    """Result of protocol verification."""
    passed: bool
    errors: List[str]
    warnings: List[str]
    
    def __bool__(self) -> bool:
        """Allow using result in boolean context."""
        return self.passed


class ProtocolVerifier:
    """Verifies protocol well-formedness."""
    
    def __init__(self, protocol: Protocol):
        """Initialize verifier with a protocol.
        
        Args:
            protocol: The protocol to verify
        """
        self.protocol = protocol
        self.errors: List[str] = []
        self.warnings: List[str] = []
    
    def verify(self) -> VerificationResult:
        """Run all well-formedness checks.
        
        Returns:
            VerificationResult with pass/fail status and any errors/warnings
        """
        self.errors = []
        self.warnings = []
        
        try:
            self.check_wf1()
            self.check_wf2()
            self.check_wf3()
            self.check_wf4()
            self.check_wf5()
        except WellFormednessViolation:
            # Violations are already recorded in self.errors
            pass
        
        return VerificationResult(
            passed=len(self.errors) == 0,
            errors=self.errors,
            warnings=self.warnings
        )
    
    def check_wf1(self) -> None:
        """WF1: Mode separation - tool sets must be disjoint across modes.
        
        Each mode should have its own distinct set of tools with no overlap.
        This ensures clear separation of concerns between operational stages.
        
        Raises:
            WF1Violation: If tool sets overlap between modes
        """
        mode_tools: Dict[str, Set[str]] = {}
        
        for mode in self.protocol.modes:
            mode_tools[mode.mode_id] = set(mode.tools)
        
        # Check all pairs of modes for tool overlap
        mode_ids = list(mode_tools.keys())
        for i in range(len(mode_ids)):
            for j in range(i + 1, len(mode_ids)):
                mode1_id = mode_ids[i]
                mode2_id = mode_ids[j]
                
                overlap = mode_tools[mode1_id] & mode_tools[mode2_id]
                if overlap:
                    error_msg = (
                        f"WF1 Violation: Modes '{mode1_id}' and '{mode2_id}' "
                        f"share tools: {sorted(overlap)}"
                    )
                    self.errors.append(error_msg)
                    raise WF1Violation(error_msg)
    
    def check_wf2(self) -> None:
        """WF2: Role uniqueness within mode - no duplicate roles per mode.
        
        While the current model doesn't directly assign roles to modes,
        this check verifies that role IDs are unique across the protocol.
        
        Raises:
            WF2Violation: If duplicate role IDs are found
        """
        role_ids: Set[str] = set()
        duplicates: List[str] = []
        
        for role in self.protocol.roles:
            if role.role_id in role_ids:
                duplicates.append(role.role_id)
            role_ids.add(role.role_id)
        
        if duplicates:
            error_msg = f"WF2 Violation: Duplicate role IDs found: {sorted(duplicates)}"
            self.errors.append(error_msg)
            raise WF2Violation(error_msg)
    
    def check_wf3(self) -> None:
        """WF3: Validator coverage - all referenced validators must be defined.

        Ensures that:
        1. All validator IDs referenced in modes exist in the protocol
        2. The initial mode exists and is valid
        3. Dispatching modes have at least one pre_execute validator

        Raises:
            WF3Violation: If validator coverage is insufficient
        """
        # Get all defined validator IDs
        defined_validators = {v.validator_id for v in self.protocol.validators}

        # Check validator references in modes
        violations: List[str] = []
        for mode in self.protocol.modes:
            for validator_id in mode.validators:
                if validator_id not in defined_validators:
                    violations.append(
                        f"Mode '{mode.mode_id}' references undefined validator '{validator_id}'"
                    )

        # Check initial mode exists
        mode_ids = {m.mode_id for m in self.protocol.modes}
        if self.protocol.initial_mode not in mode_ids:
            violations.append(
                f"Initial mode '{self.protocol.initial_mode}' is not defined"
            )

        # Phase coverage: dispatching modes must have pre_execute validators
        for mode in self.protocol.modes:
            if "dispatch" in mode.tools:
                mode_validator_ids = set(mode.validators)
                pre_execute_validators = [
                    v for v in self.protocol.validators
                    if v.validator_id in mode_validator_ids
                    and v.evaluation_phase == "pre_execute"
                ]
                if not pre_execute_validators:
                    violations.append(
                        f"Mode '{mode.mode_id}' has dispatch capability "
                        f"but no pre_execute validators"
                    )

        if violations:
            error_msg = f"WF3 Violation: {'; '.join(violations)}"
            self.errors.append(error_msg)
            raise WF3Violation(error_msg)
    
    def check_wf4(self) -> None:
        """WF4: Policy completeness - disagreement policy properly configured.
        
        Ensures the disagreement policy is valid and makes sense given
        the number of validators.
        
        Raises:
            WF4Violation: If policy configuration is invalid
        """
        num_validators = len(self.protocol.validators)
        policy = self.protocol.disagreement_policy
        
        # Check that we have enough validators for the policy
        if policy == "unanimous" and num_validators < 1:
            error_msg = "WF4 Violation: UNANIMOUS policy requires at least 1 validator"
            self.errors.append(error_msg)
            raise WF4Violation(error_msg)
        
        if policy == "majority" and num_validators < 2:
            error_msg = "WF4 Violation: MAJORITY policy requires at least 2 validators"
            self.errors.append(error_msg)
            raise WF4Violation(error_msg)
        
        if policy == "quorum" and num_validators < 3:
            # Quorum typically needs at least 3 for meaningful threshold
            warning_msg = "WF4 Warning: QUORUM policy with fewer than 3 validators may not be meaningful"
            self.warnings.append(warning_msg)
    
    def check_wf5(self) -> None:
        """WF5: Constraint consistency - constraints must be valid and non-conflicting.
        
        Checks that:
        1. All constraint IDs are unique across the protocol
        2. Constraint expressions are not empty
        3. No obviously conflicting constraints exist
        
        Raises:
            WF5Violation: If constraints are invalid or conflicting
        """
        constraint_ids: Set[str] = set()
        duplicates: List[str] = []
        invalid_expressions: List[str] = []
        errors: List[str] = []
        
        # Collect all constraints
        all_constraints: List[Constraint] = []
        all_constraints.extend(self.protocol.global_constraints)
        
        for mode in self.protocol.modes:
            all_constraints.extend(mode.constraints)
        
        for validator in self.protocol.validators:
            all_constraints.extend(validator.constraints)
        
        # Check each constraint
        for constraint in all_constraints:
            # Check for duplicate IDs
            if constraint.constraint_id in constraint_ids:
                duplicates.append(constraint.constraint_id)
            constraint_ids.add(constraint.constraint_id)
            
            # Check for empty expressions
            if not constraint.expression or not constraint.expression.strip():
                if constraint.predicate:
                    # expression is optional when predicate is set
                    self.warnings.append(
                        f"Constraint '{constraint.constraint_id}' has empty expression "
                        f"(predicate '{constraint.predicate}' is set — expression is documentation only)"
                    )
                else:
                    invalid_expressions.append(
                        f"Constraint '{constraint.constraint_id}' has empty expression"
                    )

            # Verify predicate name is registered
            if constraint.predicate:
                if constraint.predicate not in _default_registry:
                    errors.append(
                        f"Constraint '{constraint.constraint_id}' references "
                        f"unknown predicate '{constraint.predicate}'"
                    )
        
        # Compile errors from duplicates and invalid expressions
        if duplicates:
            errors.append(f"Duplicate constraint IDs: {sorted(duplicates)}")
        if invalid_expressions:
            errors.append(f"Invalid expressions: {'; '.join(invalid_expressions)}")
        
        if errors:
            error_msg = f"WF5 Violation: {'; '.join(errors)}"
            self.errors.append(error_msg)
            raise WF5Violation(error_msg)


def verify_protocol(protocol: Protocol) -> VerificationResult:
    """Convenience function to verify a protocol.
    
    Args:
        protocol: The protocol to verify
        
    Returns:
        VerificationResult with pass/fail status and any errors/warnings
    """
    verifier = ProtocolVerifier(protocol)
    return verifier.verify()