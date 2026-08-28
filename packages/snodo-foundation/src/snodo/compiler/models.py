"""Protocol syntax models for the Snodo compiler.

Pydantic models representing the abstract syntax from Section 4.1 of the paper.
All models are immutable and include validation logic.
"""

from enum import Enum
from typing import List, Optional, Dict, Any, Set
from pydantic import BaseModel, Field, field_validator, field_serializer, ConfigDict


class ExecutionConfig(BaseModel):
    """Branch execution configuration for task isolation."""

    max_retries: int = Field(default=3, ge=0, le=10)
    branch_ttl_days: int = Field(default=7, ge=1, le=30)
    branch_prefix: str = Field(default="task")
    max_recovery_depth: int = Field(
        default=3,
        ge=0,
        le=20,
        description=(
            "Maximum recovery depth per branch (default 3). Bounded against "
            "non-converging loops by recovery-stall detection and max_total_fix_attempts."
        ),
    )
    max_total_fix_attempts: int = Field(default=10, ge=1, le=100)
    auto_merge: bool = Field(
        default=False,
        description=(
            "Whether a successfully completed task's branch is merged into the "
            "base branch automatically. Default off; a mode may override it."
        ),
    )
    prepare_command: Optional[str] = Field(
        default=None,
        description=(
            "Explicit environment preparation command executed after worktree "
            "setup and before task execution/validation. None = auto-detect "
            "from lockfiles."
        ),
    )


class DisagreementPolicy(str, Enum):
    """Policy for resolving validator disagreements."""
    UNANIMOUS = "unanimous"  # All validators must pass
    MAJORITY = "majority"    # >50% must pass
    QUORUM = "quorum"        # Configurable threshold
    ANY = "any"              # At least one must pass


# Tools that confer approval/integration authority.  These are the only tools
# that WF1 requires be exclusive to a single mode (see ADR 017); a protocol may
# extend this set but may not shrink it — dropping an approval-conferring tool
# from the set would silently weaken the no-self-approval guarantee.
DEFAULT_EXCLUSIVE_TOOLS = frozenset({"approve", "merge"})


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
    judges_spec: bool = Field(
        default=False,
        description="Whether this validator's critique is about the spec's "
                    "wording (intent, constraints, scope) rather than about the "
                    "work. Only judges_spec validators' critique feeds the "
                    "spec-authoring rewriter; a non-spec objection must not "
                    "silently reshape the spec (Fixes #35)."
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
    auto_merge: Optional[bool] = Field(
        default=None,
        description=(
            "Override the protocol-level auto_merge for this mode. None = inherit "
            "the protocol's execution.auto_merge setting."
        ),
    )
    max_recovery_depth: Optional[int] = Field(
        default=None,
        ge=0,
        le=20,
        description=(
            "Override the protocol-level max_recovery_depth for this mode. None = inherit "
            "the protocol's execution.max_recovery_depth setting."
        ),
    )
    
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
    exclusive_tools: Set[str] = Field(
        default_factory=lambda: set(DEFAULT_EXCLUSIVE_TOOLS),
        description=(
            "Tools that must be exclusive to a single mode (approval-conferring). "
            "Default: approve + merge. A protocol may extend, but not shrink, this "
            "set — the defaults are always enforced."
        ),
    )
    
    @field_validator('protocol_id')
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("protocol_id cannot be empty")
        return v

    @field_validator('exclusive_tools')
    @classmethod
    def validate_exclusive_tools(cls, v: Set[str]) -> Set[str]:
        """The defaults are always enforced: a protocol may extend the
        exclusive set but never shrink it."""
        return set(v) | set(DEFAULT_EXCLUSIVE_TOOLS)

    @field_serializer('exclusive_tools')
    @classmethod
    def serialize_exclusive_tools(cls, v: Set[str]):
        """Deterministic serialization (sets have no stable order)."""
        return sorted(v)
    
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

    def resolve_mode_setting(self, mode_id: str, field_name: str) -> Any:
        """Resolve a setting for *mode_id*, falling back to protocol execution default.

        Checks mode.*field_name* (if mode exists and setting is not None), otherwise
        falls back to protocol.execution.*field_name*.
        """
        mode = self.get_mode(mode_id)
        if mode is not None:
            val = getattr(mode, field_name, None)
            if val is not None:
                return val
        return getattr(self.execution, field_name)

    def auto_merge_enabled(self, mode_id: str) -> bool:
        """Whether a successfully completed task in *mode_id* auto-merges.

        The mode's ``auto_merge`` (if set) overrides the protocol-level
        ``execution.auto_merge``; otherwise the protocol setting applies.
        """
        return bool(self.resolve_mode_setting(mode_id, "auto_merge"))

    def max_recovery_depth_for(self, mode_id: str) -> int:
        """Resolve max recovery depth for *mode_id*.

        The mode's ``max_recovery_depth`` (if set) overrides the protocol-level
        ``execution.max_recovery_depth``; otherwise the protocol setting applies.
        """
        return int(self.resolve_mode_setting(mode_id, "max_recovery_depth"))
    
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


# ---------------------------------------------------------------------------
# Plan models (Pydantic view over plan.yml and status.json)
# ---------------------------------------------------------------------------

class PlanTask(BaseModel):
    """A task entry within a plan."""

    id: str
    status: str = Field(default="pending")
    parent_task_ref: Optional[str] = Field(default=None)
    depth: int = Field(default=0, ge=0)
    spec_hash: Optional[str] = Field(default=None)

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        return default


class PlanWave(BaseModel):
    """A wave entry within a plan."""

    id: int
    depends_on: List[int] = Field(default_factory=list)
    tasks: List[str] = Field(default_factory=list)

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        return default


class Plan(BaseModel):
    """Pydantic model representing a plan structure.

    Provides a typed view over plan.yml and status.json without altering
    the on-disk format.
    """

    name: str
    intent: str
    waves: List[PlanWave] = Field(default_factory=list)
    tasks: Dict[str, PlanTask] = Field(default_factory=dict)

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        return default

    def to_dict(self) -> Dict[str, Any]:
        """Return dict representation matching plan.yml on-disk format."""
        return {
            "name": self.name,
            "intent": self.intent,
            "waves": [
                {
                    "id": w.id,
                    "depends_on": list(w.depends_on),
                    "tasks": list(w.tasks),
                }
                for w in self.waves
            ],
        }

    @classmethod
    def from_dict(
        cls,
        plan_data: Dict[str, Any],
        status_data: Optional[Dict[str, Any]] = None,
    ) -> "Plan":
        """Construct a Plan model from raw plan_data dict and optional status_data dict.

        Handles legacy string entries and dict task entries in status_data.
        """
        name = str(plan_data.get("name") or "")
        intent = str(plan_data.get("intent") or "")
        raw_waves = plan_data.get("waves") or []

        waves: List[PlanWave] = []
        wave_task_ids: List[str] = []
        for w in raw_waves:
            if isinstance(w, dict):
                wid = w.get("id")
                deps = w.get("depends_on") or []
                tasks = w.get("tasks") or []
                try:
                    wid_int = int(wid) if wid is not None else 0
                except (ValueError, TypeError):
                    wid_int = 0
                deps_int = []
                for d in deps:
                    try:
                        deps_int.append(int(d))
                    except (ValueError, TypeError):
                        pass
                str_tasks = [str(t) for t in tasks]
                waves.append(PlanWave(id=wid_int, depends_on=deps_int, tasks=str_tasks))
                wave_task_ids.extend(str_tasks)

        status_tasks = (status_data or {}).get("tasks", {})
        if not isinstance(status_tasks, dict):
            status_tasks = {}

        tasks_map: Dict[str, PlanTask] = {}

        # Populate tasks from waves first
        for tid in wave_task_ids:
            tasks_map[tid] = PlanTask(id=tid, status="pending")

        # Merge status_tasks
        for tid, entry in status_tasks.items():
            tid_str = str(tid)
            if isinstance(entry, str):
                status_str = entry
                parent_ref = None
                depth_val = 0
                hash_val = None
            elif isinstance(entry, dict):
                status_str = str(entry.get("status", "pending"))
                parent_ref = entry.get("parent_task_ref")
                if parent_ref is not None:
                    parent_ref = str(parent_ref)
                try:
                    depth_val = int(entry.get("depth", 0))
                except (ValueError, TypeError):
                    depth_val = 0
                hash_val = entry.get("spec_hash")
                if hash_val is not None:
                    hash_val = str(hash_val)
            else:
                status_str = "pending"
                parent_ref = None
                depth_val = 0
                hash_val = None

            tasks_map[tid_str] = PlanTask(
                id=tid_str,
                status=status_str,
                parent_task_ref=parent_ref,
                depth=depth_val,
                spec_hash=hash_val,
            )

        return cls(name=name, intent=intent, waves=waves, tasks=tasks_map)