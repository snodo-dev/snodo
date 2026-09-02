"""Well-formedness checkers for protocol verification.

Implements static checks based on Section 4.4 Well-Formedness Conditions.
"""

from pathlib import Path
from typing import List, Set, Dict, Optional
from dataclasses import dataclass

from snodo.compiler.models import Protocol, Constraint, Plan
import snodo.predicates.scope  # noqa: F401 — ensures predicates self-register
import snodo.predicates.tests  # noqa: F401
import snodo.predicates.secrets  # noqa: F401
from snodo.predicates.registry import _default_registry


class WellFormednessViolation(Exception):
    """Base exception for well-formedness violations."""


class WF1Violation(WellFormednessViolation):
    """WF1: Mode separation violation - exclusive tool held by multiple modes."""


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
        """WF1: Mode separation — exclusive tools must appear in at most one mode.

        The invariant WF1 exists to guarantee is **no self-approval**: an
        approval-conferring tool must not be reachable from two different modes,
        or a single actor could approve its own work.  INV2 already bounds
        capability to the active mode, so total tool disjointness is not required
        for the boundary to hold; what disjointness uniquely provided was the
        ability to infer the mode from the tool alone, which the audit log now
        records explicitly (see ADR 017).

        Consequently only the *exclusive* tool set (``Protocol.exclusive_tools``,
        defaulting to ``approve`` + ``merge``) must be disjoint across modes.
        Non-exclusive tools may be held by any number of modes.

        Raises:
            WF1Violation: If an exclusive tool appears in more than one mode
        """
        # Map each tool to the set of modes that hold it.
        tool_modes: Dict[str, List[str]] = {}
        for mode in self.protocol.modes:
            for tool in mode.tools:
                tool_modes.setdefault(tool, []).append(mode.mode_id)

        for tool in sorted(self.protocol.exclusive_tools):
            holders = tool_modes.get(tool, [])
            if len(holders) > 1:
                error_msg = (
                    f"WF1 Violation: Exclusive tool '{tool}' is held by "
                    f"multiple modes: {sorted(holders)}"
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

        # Quality validators must never have severity_cap configured
        for v in self.protocol.validators:
            if v.validator_type == "quality" and v.severity_cap is not None:
                violations.append(
                    f"Quality validator '{v.validator_id}' cannot specify severity_cap "
                    f"'{v.severity_cap.value}'. Quality gates execute the test suite and must not be capped."
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
        
        # A single post_execute validator under UNANIMOUS is an unopposed veto
        # over completed work: the phase is evaluated with total_count == 1, so
        # that validator's verdict alone decides whether the work passes —
        # including operational noise from a quality/acceptance judge (Fixes #41).
        if policy == "unanimous":
            post_execute = [
                v.validator_id for v in self.protocol.validators
                if v.evaluation_phase == "post_execute"
            ]
            if len(post_execute) == 1:
                warning_msg = (
                    "WF4 Warning: UNANIMOUS policy with exactly one POST_EXECUTE "
                    f"validator ({post_execute[0]}) gives it an unopposed veto "
                    "over completed work. It is the only post-execute judge, so "
                    "a single warn/blocker — including operational noise — "
                    "blocks every task. Add a second post-execute validator or "
                    "use a different policy."
                )
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


@dataclass
class PlanVerificationResult:
    """Result of plan verification."""

    passed: bool
    errors: List[str]
    warnings: List[str]

    def __bool__(self) -> bool:
        return self.passed


class PlanWellFormednessError(Exception):
    """Raised when a plan fails well-formedness verification at load time."""

    def __init__(self, violations: List[str]):
        self.violations = violations
        super().__init__(
            "Plan violates well-formedness conditions:\n  - "
            + "\n  - ".join(violations)
        )


def verify_plan(plan: Plan, plan_dir: Optional[Path] = None) -> PlanVerificationResult:
    """Verify well-formedness of a Plan model.

    Checks:
    1. Basic structure (intent present, waves defined)
    2. Unknown parent_task_ref references
    3. Parent task reference cycles and wave dependency cycles
    4. Wave-number gaps (e.g. waves 1, 3 without 2)
    5. Status entries with no matching task in plan waves
    6. Tasks with missing spec files (when plan_dir is provided)
    7. Unknown wave dependency references

    Args:
        plan: Plan model instance
        plan_dir: Optional Path to plan directory on disk

    Returns:
        PlanVerificationResult containing passed, errors, and warnings.
    """
    errors: List[str] = []
    warnings: List[str] = []

    if plan.parse_errors:
        errors.extend(plan.parse_errors)

    if not plan.intent:
        errors.append("Missing intent")

    if not plan.waves:
        errors.append("No waves defined")

    wave_ids = [w.id for w in plan.waves]
    wave_id_set = set(wave_ids)

    # Check wave-number gaps: wave IDs should be contiguous 1..N starting at 1
    has_wave_id_parse_error = any("Wave id " in e for e in plan.parse_errors)
    if wave_ids and not has_wave_id_parse_error:
        sorted_wave_ids = sorted(wave_ids)
        expected_range = list(range(1, len(sorted_wave_ids) + 1))
        if sorted_wave_ids != expected_range:
            errors.append(
                f"Wave-number gap detected: expected contiguous 1..{len(sorted_wave_ids)}, found {sorted_wave_ids}"
            )

    # Check wave dependencies and gather task IDs
    wave_map = {w.id: w for w in plan.waves}
    wave_task_ids: Set[str] = set()

    for w in plan.waves:
        if not w.tasks:
            warnings.append(f"Wave {w.id} has no tasks")
        for t in w.tasks:
            wave_task_ids.add(t)

        for dep in w.depends_on:
            if dep not in wave_id_set:
                errors.append(f"Wave {w.id} depends on unknown wave {dep}")

    # Check for wave dependency cycles
    for w_id in wave_map:
        visited_waves: Set[int] = set()
        stack = list(wave_map[w_id].depends_on)
        while stack:
            curr_dep = stack.pop(0)
            if curr_dep == w_id or curr_dep in visited_waves:
                errors.append(f"Wave dependency cycle detected involving wave {w_id}")
                break
            visited_waves.add(curr_dep)
            if curr_dep in wave_map:
                stack.extend(wave_map[curr_dep].depends_on)

    # Check status entries with no matching task in plan waves
    for task_id in plan.tasks:
        if task_id not in wave_task_ids:
            errors.append(f"Status entry '{task_id}' has no matching task in plan waves")

    # Check unknown parent_task_ref and parent task cycles
    all_known_tasks = set(plan.tasks.keys()).union(wave_task_ids)
    for task_id, task in plan.tasks.items():
        pref = task.parent_task_ref
        if pref:
            if pref not in all_known_tasks:
                errors.append(f"Task '{task_id}' references unknown parent_task_ref '{pref}'")

    # Check for parent reference cycles
    cycle_nodes_reported: Set[str] = set()
    for task_id in plan.tasks:
        if task_id in cycle_nodes_reported:
            continue
        visited: Set[str] = {task_id}
        curr = plan.tasks[task_id].parent_task_ref
        while curr:
            if curr in visited:
                errors.append(f"Parent reference cycle detected involving task '{task_id}'")
                cycle_nodes_reported.update(visited)
                break
            visited.add(curr)
            if curr in plan.tasks:
                curr = plan.tasks[curr].parent_task_ref
            else:
                break

    # Check tasks with no spec file (when plan_dir provided)
    if plan_dir and plan_dir.exists():
        for w in plan.waves:
            wave_dir = plan_dir / f"wave_{w.id}"
            for task_id in w.tasks:
                spec_file = wave_dir / f"{task_id}_task.md"
                if not spec_file.exists():
                    errors.append(f"Missing spec: {task_id}")

    return PlanVerificationResult(
        passed=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


def verify_plan_dir(plan_dir: Path) -> PlanVerificationResult:
    """Load and verify a plan directory on disk.

    Args:
        plan_dir: Path to the plan directory (containing plan.yml and wave_* dirs)

    Returns:
        PlanVerificationResult containing passed, errors, and warnings.
    """
    import json
    import yaml

    if not plan_dir.exists():
        return PlanVerificationResult(
            passed=False,
            errors=[f"Plan directory does not exist: {plan_dir}"],
            warnings=[],
        )
    if not plan_dir.is_dir():
        return PlanVerificationResult(
            passed=False,
            errors=[f"Plan path is not a directory: {plan_dir}"],
            warnings=[],
        )

    plan_file = plan_dir / "plan.yml"
    if not plan_file.exists():
        return PlanVerificationResult(
            passed=False,
            errors=[f"plan.yml not found in: {plan_dir}"],
            warnings=[],
        )

    try:
        with open(plan_file) as f:
            plan_data = yaml.safe_load(f) or {}
    except Exception as e:
        return PlanVerificationResult(
            passed=False,
            errors=[f"Failed to parse plan.yml: {e}"],
            warnings=[],
        )

    status_file = plan_dir / "status.json"
    status_data = {}
    if status_file.exists():
        try:
            with open(status_file) as f:
                status_data = json.load(f) or {}
        except Exception as e:
            return PlanVerificationResult(
                passed=False,
                errors=[f"Failed to parse status.json: {e}"],
                warnings=[],
            )

    try:
        plan = Plan.from_dict(plan_data, status_data)
    except Exception as e:
        return PlanVerificationResult(
            passed=False,
            errors=[f"Invalid plan structure: {e}"],
            warnings=[],
        )

    return verify_plan(plan, plan_dir=plan_dir)
