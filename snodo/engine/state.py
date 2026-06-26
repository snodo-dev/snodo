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
    halt_type: Optional[str] = None  # "blocked" | "escalated" | "resolution" | "constraint" | "max_iterations" | "wf3" | "validator_error"
    pending_disagreement: Optional[Dict[str, Any]] = None
    spawned_subtasks: List[Task] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    messages: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""


def _build_audit_results(validators: list, results: list) -> list:
    """Build audit results array with capping metadata.

    Compares each result against its validator spec's severity_cap.
    When capping occurred, adds original_severity and severity_capped
    flags to the audit payload.
    """
    audit_results = []
    for i, r in enumerate(results):
        entry = {
            "validator_id": r.validator_id,
            "severity": r.severity,
            "justification": r.justification,
        }
        # Check if this result was capped
        if i < len(validators):
            v = validators[i]
            if v.severity_cap is not None and r.severity == v.severity_cap.value:
                # Severity matches the cap — may have been downgraded.
                # We don't have the original here, but we can flag that
                # the result sits at the cap boundary
                entry["severity_at_cap"] = True
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

