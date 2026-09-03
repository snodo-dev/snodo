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


class NoFileOperationsError(ExecutionError):
    """The coder completed successfully but produced no file operations."""


class Coder(ABC):
    """Implements tasks. Can be LLM or human or traditional tooling.

    The engine offers every adapter several optional capabilities (a progress
    sink, a workspace, a job/task id for correlation, and two behavioural
    switches). These are DECLARED here, with defaults, so that "this adapter
    does not support X" is a visible fact rather than a silently skipped
    ``hasattr`` line (docs/architecture/coder-adapter-contract.md §3.1, #68).
    An adapter that does not override a capability inherits the default; the
    engine sets these attributes unconditionally, never behind a guard.
    """

    #: Workspace the coder reads/writes, injected by the engine when the task
    #: runs under a workspace. None for adapters that do not use one.
    workspace_mcp: Optional[Any] = None
    #: Progress sink handed to the coder by the engine; an adapter that wants
    #: per-turn progress emits here. None = the adapter reports no progress.
    progress_callback: Optional[Any] = None
    #: When True, the coder writes its changes to the working tree directly
    #: and the executor must NOT replay the returned artifacts through
    #: WorkspaceMCP (e.g. in-place adapters). Default False: the executor
    #: writes the artifacts.
    skip_workspace_write: bool = False
    #: When True, the coder (or its base class) owns the commit and the
    #: executor must NOT stage/commit. This does NOT waive the obligation that
    #: produced work be observable and attributable — "coder produced nothing"
    #: is a fault regardless of who commits. Default False: the executor
    #: commits.
    skip_engine_commit: bool = False
    #: Correlation ids the engine injects so adapter-side logging/telemetry
    #: can be attributed to a job and task. Empty when not set.
    _job_id: str = ""
    _task_id: str = ""
    #: Recovery depth and attempt number (1-based) of the task being executed,
    #: injected by the engine so per-turn telemetry can be grouped by depth.
    _depth: int = 0
    _attempt: int = 1
    #: Model identifier the adapter is bound to. Used for default-model
    #: resolution and coder-respawn checks; may be empty on simple adapters.
    model: str = ""
    #: When True, the coder has access to and observes test execution feedback
    #: during implementation (e.g. LiteLLMAdapter with test runner access).
    #: Default False: the coder does not observe tests.
    observes_tests: bool = False

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
    # The original task at the root of a recovery chain.  Recovery subtasks
    # derive their id (``<root>_fix_N``) and their spec (original intent +
    # accumulated failures) from the root, never from the immediately previous
    # attempt — see ADR 021.
    root_task_ref: Optional[str] = None
    root_spec: Optional[str] = None
    prior_failures: List[Dict[str, Any]] = Field(default_factory=list)
    # Recovery provenance: files earlier attempts in the same recovery chain
    # wrote in the cumulative worktree. This is ownership context, not a
    # rewrite request.
    attempt_provenance: List[Dict[str, Any]] = Field(default_factory=list)
    # Recovery read-set: paths earlier attempts inspected (files read,
    # directories listed). Paths only, never contents — the tree changes
    # between attempts and a cached version must not become authoritative.
    attempt_reads: List[Dict[str, Any]] = Field(default_factory=list)
    depth: int = 0
    flow_type: Optional[str] = None
    wave_id: Optional[str] = None


class ValidatorResult(BaseModel):
    """Output from a single validator."""
    validator_id: str
    severity: Literal["pass", "warn", "blocker"]
    justification: str
    error: bool = False
    cited_criteria: Optional[List[str]] = None
    #: Pre-cap severity when a severity_cap downgraded this result; None otherwise.
    severity_original: Optional[str] = None


class TaskSpec(BaseModel):
    """Specification for code generation."""
    description: str
    constraints: List[str]
    memory_summary: str = ""
    project_context: Dict[str, Any] = Field(default_factory=dict)


class FileArtifact(BaseModel):
    """A file operation emitted by the coder."""
    path: str
    content: str = ""
    action: str = "write"  # "write" | "delete"


class CodeArtifact(BaseModel):
    """Generated code output — list of file operations."""
    files: List[FileArtifact] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
