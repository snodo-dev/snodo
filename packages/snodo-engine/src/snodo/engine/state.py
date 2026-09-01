"""Engine loop state and stage definition.

FILE: snodo/engine/state.py
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from enum import Enum

from snodo.core.interfaces import Task, ValidatorResult
from snodo.infrastructure.tokens import ValidationToken


class LoopStage(str, Enum):
    """Stages in the orchestration loop."""
    GOVERNANCE = "governance"
    VALIDATE = "validate"
    EXECUTE = "execute"
    MOVE_NEXT = "move_next"
    COMPLETE = "complete"
    BLOCKED = "blocked"


@dataclass
class LoopState:
    """State carried through the orchestration loop."""
    task: Task
    current_mode: str
    validation_results: List[ValidatorResult] = field(default_factory=list)
    validation_token: Optional[ValidationToken] = None
    artifacts: List[str] = field(default_factory=list)
    stage: LoopStage = LoopStage.GOVERNANCE
    iteration: int = 0
    constraints_passed: bool = True
    constraint_violations: List[str] = field(default_factory=list)
    policy_decision: Optional[Any] = None
    is_complete: bool = False
    is_blocked: bool = False
    halt_type: Optional[str] = None  # "blocked" | "escalated" | "resolution" | "constraint" | "max_iterations" | "turn_budget_exhausted" | "wf3" | "validator_error" | "recovery_exhausted"
    pending_disagreement: Optional[Dict[str, Any]] = None
    spawned_subtasks: List[Task] = field(default_factory=list)
    needs_recovery: bool = False
    needs_spec_authoring: bool = False
    spec_authoring_attempts: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    messages: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    #: HEAD sha captured in the execute node before the coder runs; the
    #: post-execute judges diff base_ref..HEAD. None when no git workspace.
    base_ref: Optional[str] = None


def _build_audit_results(
    validators: list, results: list, cap_originals: Optional[dict] = None
) -> list:
    """Build audit results array with capping metadata.

    Results are paired to validators by ``validator_id``, never positionally:
    ``run_validators`` returns results keyed by id, so a filtered or reordered
    validators list must not silently misattribute a cap.

    ``severity_at_cap`` is set only when a cap actually fired — i.e. the
    validator's id appears in *cap_originals* (the pre-cap severity recorded
    by ``run_validators``).  A result whose severity merely equals the cap
    value is genuine output, not evidence of a downgrade.  *cap_originals* is
    the authoritative record of a cap; the field names are kept stable for
    consumers.
    """
    validators_by_id = {v.validator_id: v for v in validators}
    audit_results = []
    for r in results:
        entry = {
            "validator_id": r.validator_id,
            "severity": r.severity,
            "justification": r.justification,
        }
        v = validators_by_id.get(r.validator_id)
        if v is not None and cap_originals and r.validator_id in cap_originals:
            entry["severity_at_cap"] = True
            entry["severity_original"] = cap_originals[r.validator_id]
        audit_results.append(entry)
    return audit_results


def _slugify(spec: str, max_words: int = 5) -> str:
    """Convert a task spec into a branch-safe slug."""
    import re
    words = spec.strip().split()[:max_words]
    slug = "-".join(words).lower()
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    return slug


def _task_branch_name(task_id: str, spec: str) -> str:
    """Build a branch name: task/{task_id}/{slug}."""
    return f"task/{task_id}/{_slugify(spec)}"


def _branch_exists(git_mcp: Any, name: str) -> bool:
    """Return True if name is an existing branch head."""
    try:
        return name in git_mcp.repo.heads
    except Exception:
        return False

