"""Protocol syntax models for the Snodo compiler.

Pydantic models representing the abstract syntax from Section 4.1 of the paper.
All models are immutable and include validation logic.
"""

from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator, ConfigDict


class ExecutionConfig(BaseModel):
    """Branch execution configuration for task isolation."""

    max_retries: int = Field(default=3, ge=0, le=10)
    branch_ttl_days: int = Field(default=7, ge=1, le=30)
    branch_prefix: str = Field(default="task")


class DisagreementPolicy(str, Enum):
    """Policy for resolving validator disagreements."""
    UNANIMOUS = "unanimous"  # All validators must pass
    MAJORITY = "majority"    # >50% must pass
    QUORUM = "quorum"        # Configurable threshold
    ANY = "any"              # At least one must pass


class Severity(str, Enum):
    """Validator result severity levels.

    Ordered: PASS < WARN < BLOCKER.  Explicit comparison operators
    override the str-inherited lexicographic ordering.
    """
    PASS = "pass"
    WARN = "warn"
    BLOCKER = "blocker"

    # Intentional LSP violation: we override str's comparison operators
    # to provide semantic ordering (pass < warn < blocker) rather than
    # lexicographic ordering. This is required for policy evaluation.
    def __lt__(self, other: "Severity") -> bool:  # type: ignore[override]
        _order = {"pass": 0, "warn": 1, "blocker": 2}
        return _order[self.value] < _order[other.value]

    def __le__(self, other: "Severity") -> bool:  # type: ignore[override]
        _order = {"pass": 0, "warn": 1, "blocker": 2}
        return _order[self.value] <= _order[other.value]

    def __gt__(self, other: "Severity") -> bool:  # type: ignore[override]
        _order = {"pass": 0, "warn": 1, "blocker": 2}
        return _order[self.value] > _order[other.value]

    def __ge__(self, other: "Severity") -> bool:  # type: ignore[override]
        _order = {"pass": 0, "warn": 1, "blocker": 2}
        return _order[self.value] >= _order[other.value]


EVALUATION_PHASES = {"pre_execute", "post_execute", "mode_transition"}

# Fixed read-only tool names that validators may be granted.
# No write/exec/mutating tool is ever accepted here.
_READ_ONLY_TOOL_NAMES = {
    "read_file",
    "read_file_lines",
    "list_files",
    "git_show",
    "git_log",
    "read_diff_between_refs",
}


class Constraint(BaseModel):
    """A rule or limitation on protocol execution."""
    
    model_config = ConfigDict(frozen=True)
    
    constraint_id: str = Field(..., description="Unique constraint identifier")
    description: str = Field(..., description="Human-readable constraint description")
    expression: str = Field(default="", description="Boolean expression string (legacy; summary when predicate is set)")
    predicate: str = Field(default="", description="Predicate name to evaluate this constraint")
    params: Dict[str, Any] = Field(default_factory=dict, description="Parameters passed to the predicate")
    severity: Severity = Field(default=Severity.BLOCKER, description="Impact if violated")
    
    @field_validator('constraint_id')
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("constraint_id cannot be empty")
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError("constraint_id must be alphanumeric with - or _")
        return v


class Validator(BaseModel):
    """Evaluation criteria for tasks."""

    model_config = ConfigDict(frozen=True)

    validator_id: str = Field(..., description="Unique validator identifier")
    validator_type: str = Field(..., description="Type of validation (e.g., security, architecture)")
    criteria: List[str] = Field(default_factory=list, description="Evaluation criteria")
    constraints: List[Constraint] = Field(default_factory=list, description="Additional constraints")
    evaluation_phase: str = Field(
        default="pre_execute",
        description="When to run this validator (e.g., pre_execute, post_execute)"
    )
    tooling: Dict[str, Any] = Field(
        default_factory=dict,
        description="Tooling configuration (e.g., test_command, timeout)"
    )
    severity_cap: Optional[Severity] = Field(
        default=None,
        description="Maximum severity this validator can emit.  Useful for "
                    "validators under evaluation: blocker capped to warn "
                    "prevents blocking the workflow.  None = no cap."
    )
    tools: List[str] = Field(
        default_factory=list,
        description="Read-only tool allowlist for this validator. "
                    "Empty means no tool access (single-completion path). "
                    f"Allowed: {sorted(_READ_ONLY_TOOL_NAMES)}"
    )
    model: Optional[str] = Field(
        default=None,
        description="Optional LLM model override for this validator. "
                    "Falls back to coder model / default_model if not set."
    )

    @field_validator('validator_id')
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("validator_id cannot be empty")
        return v

    @field_validator('validator_type')
    @classmethod
    def validate_type(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("validator_type cannot be empty")
        return v

    @field_validator('evaluation_phase')
    @classmethod
    def validate_phase(cls, v: str) -> str:
        if v not in EVALUATION_PHASES:
            raise ValueError(
                f"evaluation_phase must be one of {sorted(EVALUATION_PHASES)}, got '{v}'"
            )
        return v

    @field_validator('tools')
    @classmethod
    def validate_tools(cls, v: List[str]) -> List[str]:
        """Reject any tool name not in the fixed read-only set."""
        for tool_name in v:
            if tool_name not in _READ_ONLY_TOOL_NAMES:
                raise ValueError(
                    f"Validator tool '{tool_name}' is not a read-only tool. "
                    f"Allowed tools: {sorted(_READ_ONLY_TOOL_NAMES)}. "
                    f"Validators may never use write/exec/mutating tools."
                )
        return v


class Role(BaseModel):
    """Participant role in the protocol."""
    
    model_config = ConfigDict(frozen=True)
    
    role_id: str = Field(..., description="Unique role identifier")
    name: str = Field(..., description="Human-readable role name")
    permissions: List[str] = Field(default_factory=list, description="Allowed actions")
    responsibilities: List[str] = Field(default_factory=list, description="Expected duties")
    
    @field_validator('role_id')
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("role_id cannot be empty")
        return v


class Mode(BaseModel):
    """Operational stage with defined permissions and transitions.

    Transitions are DECLARATIVE only — they document the protocol's
    intended mode handoffs but are NOT executed by the engine at
    runtime.  The engine runs single-mode per invocation; cross-mode
    handoffs are explicit user actions (snodo mode change <m>).

    Transitions ARE read by ProtocolAdherenceValidator to provide
    mode-profile context to the LLM.
    """
    model_config = ConfigDict(frozen=True)

    mode_id: str = Field(..., description="Unique mode identifier")
    name: str = Field(..., description="Human-readable mode name")
    tools: List[str] = Field(default_factory=list, description="Available tools in this mode")
    transitions: Dict[str, str] = Field(default_factory=dict, description="Declarative event → target mode mappings (not engine-executed)")
    validators: List[str] = Field(default_factory=list, description="Active validator IDs")
    constraints: List[Constraint] = Field(default_factory=list, description="Mode-specific constraints")
    coder: Optional[str] = Field(default=None, description="Coder backend name (e.g., 'litellm', 'mock')")
    coder_config: Dict[str, Any] = Field(default_factory=dict, description="Coder backend configuration")
    
    @field_validator('mode_id')
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("mode_id cannot be empty")
        return v
    
    @field_validator('transitions')
    @classmethod
    def validate_transitions(cls, v: Dict[str, str]) -> Dict[str, str]:
        for event, target in v.items():
            if not event or not target:
                raise ValueError("transitions must have non-empty event and target")
        return v


class Protocol(BaseModel):
    """Top-level protocol definition."""
    
    model_config = ConfigDict(frozen=True)
    
    protocol_id: str = Field(..., description="Unique protocol identifier")
    name: str = Field(..., description="Human-readable protocol name")
    version: str = Field(default="1.0.0", description="Protocol version")
    modes: List[Mode] = Field(..., description="Available operational modes", min_length=1)
    roles: List[Role] = Field(default_factory=list, description="Participant roles")
    validators: List[Validator] = Field(..., description="Validation agents", min_length=1)
    disagreement_policy: DisagreementPolicy = Field(
        default=DisagreementPolicy.UNANIMOUS,
        description="How to resolve validator conflicts"
    )
    initial_mode: str = Field(..., description="Starting mode ID")
    global_constraints: List[Constraint] = Field(
        default_factory=list,
        description="Protocol-wide constraints"
    )
    execution: ExecutionConfig = Field(
        default_factory=ExecutionConfig,
        description="Branch isolation and retry configuration"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    
    @field_validator('protocol_id')
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("protocol_id cannot be empty")
        return v
    
    @field_validator('initial_mode')
    @classmethod
    def validate_initial_mode(cls, v: str, info) -> str:
        """Ensure initial_mode references a valid mode."""
        # Note: Cross-field validation happens in model_validator
        if not v or not v.strip():
            raise ValueError("initial_mode cannot be empty")
        return v
    
    @field_validator('modes')
    @classmethod
    def validate_unique_mode_ids(cls, v: List[Mode]) -> List[Mode]:
        """Ensure all mode IDs are unique."""
        ids = [m.mode_id for m in v]
        if len(ids) != len(set(ids)):
            raise ValueError("mode IDs must be unique")
        return v
    
    @field_validator('validators')
    @classmethod
    def validate_unique_validator_ids(cls, v: List[Validator]) -> List[Validator]:
        """Ensure all validator IDs are unique."""
        ids = [val.validator_id for val in v]
        if len(ids) != len(set(ids)):
            raise ValueError("validator IDs must be unique")
        return v
    
    def get_mode(self, mode_id: str) -> Optional[Mode]:
        """Retrieve a mode by ID."""
        for mode in self.modes:
            if mode.mode_id == mode_id:
                return mode
        return None
    
    def get_validator(self, validator_id: str) -> Optional[Validator]:
        """Retrieve a validator by ID."""
        for validator in self.validators:
            if validator.validator_id == validator_id:
                return validator
        return None
    
    def get_role(self, role_id: str) -> Optional[Role]:
        """Retrieve a role by ID."""
        for role in self.roles:
            if role.role_id == role_id:
                return role
        return None

    def get_validators_by_phase(self, phase: str) -> List[Validator]:
        """Retrieve all validators for a given evaluation phase.

        Args:
            phase: Evaluation phase (e.g., "pre_execute", "post_execute")

        Returns:
            List of validators matching the phase.
        """
        return [v for v in self.validators if v.evaluation_phase == phase]