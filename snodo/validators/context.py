"""Validator context and base class.

FILE: snodo/validators/context.py (Task 7.11 + 7.20)

Carries the union of context fields all validators need.
Build ONCE per validate pass; each validator reads only
the fields it uses.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from snodo.core.interfaces import Task, ValidatorResult


@dataclass
class ValidatorContext:
    """Execution context for validator evaluation."""
    task: Task
    current_mode: Any = None
    protocol: Any = None
    artifacts: List[str] = field(default_factory=list)
    audit_log: Optional[Any] = None
    mode_name: str = ""
    mode_tools: list = field(default_factory=list)
    mode_transitions: Dict[str, str] = field(default_factory=dict)
    mode_validator_refs: list = field(default_factory=list)
    completion_fn: Any = None
    model: str = ""
    working_directory: str = ""
    workspace_mcp: Any = None
    git_mcp: Any = None
    phase: str = ""


class ValidatorBase(ABC):
    """Abstract base for all validator backends.

    Subclasses implement evaluate(context) → ValidatorResult.
    """

    @abstractmethod
    def evaluate(self, context: ValidatorContext) -> ValidatorResult:
        """Evaluate the task against this validator's criteria."""
        ...

    @classmethod
    @abstractmethod
    def registered_type(cls) -> str:
        """Return the validator_type string this class handles."""
        ...
