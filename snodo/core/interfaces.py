"""Core interfaces for the Snodo protocol engine.

All other modules implement against these contracts.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Literal, Optional
from dataclasses import dataclass, field

from pydantic import BaseModel


class Coder(ABC):
    """Implements tasks. Can be LLM or human or traditional tooling."""
    
    @abstractmethod
    def implement(self, spec: 'TaskSpec') -> 'CodeArtifact':
        """Generate code from specification."""


class MCPServer(ABC):
    """Tool boundary enforcement."""
    
    @abstractmethod
    def execute_tool(self, tool_name: str, params: Dict[str, Any]) -> Any:
        """Execute tool within capability boundary."""


@dataclass
class Task:
    """A unit of work."""
    id: str
    spec: str
    parent_task_ref: Optional[str] = None
    depth: int = 0


class ValidatorResult(BaseModel):
    """Output from a single validator."""
    validator_id: str
    severity: Literal["pass", "warn", "blocker", "error"]
    justification: str


@dataclass
class ExecutionResult:
    """Output from task execution."""
    task_id: str
    status: str
    artifacts: List[str]


@dataclass
class Mode:
    """Operational stage with defined permissions."""
    mode_id: str
    tools: List[str]


@dataclass
class Event:
    """State transition trigger."""
    event_type: str
    data: Dict[str, Any]


@dataclass
class TaskSpec:
    """Specification for code generation."""
    description: str
    constraints: List[str]
    memory_summary: str = ""
    project_context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FileArtifact:
    """A file operation emitted by the coder."""
    path: str
    content: str
    action: str = "write"  # "write" | "delete"


@dataclass
class CodeArtifact:
    """Generated code output — list of file operations."""
    files: list  # List[FileArtifact]
