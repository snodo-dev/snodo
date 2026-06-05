"""Core interfaces for the Snodo protocol engine.

All other modules implement against these contracts.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class AuditError(Exception):
    """Audit log operation failed (e.g., I/O write failure)."""


class ExecutionError(Exception):
    """Task execution produced no usable artifacts."""


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


class Task(BaseModel):
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


class TaskSpec(BaseModel):
    """Specification for code generation."""
    description: str
    constraints: List[str]
    memory_summary: str = ""
    project_context: Dict[str, Any] = Field(default_factory=dict)


class FileArtifact(BaseModel):
    """A file operation emitted by the coder."""
    path: str
    content: str
    action: str = "write"  # "write" | "delete"


class CodeArtifact(BaseModel):
    """Generated code output — list of file operations."""
    files: List[FileArtifact] = Field(default_factory=list)
