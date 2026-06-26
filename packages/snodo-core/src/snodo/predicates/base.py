"""Predicate ABC, context, and result types.

FILE: snodo/predicates/base.py (Task 7.8)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PredicateResult:
    """Outcome of a single predicate evaluation."""
    passed: bool
    justification: str
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PredicateContext:
    """Execution context passed to predicates at evaluation time.

    Built by the engine from LoopState + current phase.
    Predicates read context, never mutate it.

    artifacts is List[str] (paths only), matching LoopState.artifacts.
    """
    task: Any                       # Task (id, spec, parent_task_ref, depth)
    mode: str                       # current_mode_id
    artifacts: List[str]            # file paths produced so far
    workspace_mcp: Optional[Any] = None   # WorkspaceMCP or None
    git_mcp: Optional[Any] = None         # GitMCP or None
    protocol: Any = None            # Protocol object (for cross-referencing)
    phase: str = "governance"       # "governance" | "post_validate"


class Predicate(ABC):
    """Abstract base class for all constraint predicates.

    Predicates are deterministic checks — no LLM calls, no I/O side effects
    (reads are allowed, writes are not).  Each predicate must handle both
    governance and post_validate phases, passing trivially when context is
    insufficient.
    """

    @abstractmethod
    def evaluate(self, context: PredicateContext, **params: Any) -> PredicateResult:
        """Evaluate the predicate against the given context.

        Args:
            context: Execution context from the engine.
            **params: Constraint-specific parameters from the YAML.

        Returns:
            PredicateResult with passed/justification/evidence.
        """
        ...
