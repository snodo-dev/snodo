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
    #: Severity of the verdict, or None if no verdict (abstained). This is the
    #: only safe way to determine whether a verdict exists — abstentions make
    #: severity architecturally absent, not hidden behind a flag.
    severity: Optional[Literal["pass", "warn", "blocker"]] = None
    justification: str
    error: bool = False
    cited_criteria: Optional[List[str]] = None
    #: Pre-cap severity when a severity_cap downgraded this result; None otherwise.
    severity_original: Optional[str] = None
    #: True when the validator's gate was skipped rather than genuinely
    #: exercised (e.g. the quality validator ran the no-op default because no
    #: test command is configured). Severity stays "pass" so the task proceeds,
    #: but a skipped result is surfaced in normal run output and must never be
    #: mistaken for real verification evidence.
    skipped: bool = False
    #: Reason for abstention (e.g., "exhausted budget after 20 turns"). Empty if not abstained.
    abstention_reason: Optional[str] = None
    #: What an abstaining judge examined before its budget ran out — an ordered
    #: summary of the tool calls it made ("turn 3: read_file src/auth.py").
    #: Only ever set alongside severity=None; a judge that decided has no
    #: unfinished inspection to report.
    examined: Optional[List[str]] = None
    #: Read-only tools the judge was granted but never exercised when its budget
    #: ran out — the honest "what was NOT examined" half of an abstention.
    unexamined_tools: Optional[List[str]] = None

    def abstained(self) -> bool:
        """True when this result carries no verdict (severity is None).

        A method, not a field: abstention is the absence of a verdict, so it
        cannot be a value any severity comparison could confuse with one.
        """
        return self.severity is None and not self.error

    def record(self) -> Dict[str, Any]:
        """The canonical audit/display record for this result. See result_record."""
        return result_record(self)


def result_record(result: Any) -> Dict[str, Any]:
    """The canonical audit/display record for a validator result.

    Every site that records, serialises or displays what a validator concluded
    goes through this one function, so an abstention reads as "no verdict,
    here is why, and here is what was and was not examined" everywhere at
    once — never as a pass (Fixes #252).  Attribute-based so faithful test
    doubles of ValidatorResult serialise identically to real instances.
    """
    out: Dict[str, Any] = {
        "validator_id": getattr(result, "validator_id", ""),
        "severity": getattr(result, "severity", None),
        "justification": getattr(result, "justification", ""),
    }
    if out["severity"] is None:
        reason = getattr(result, "abstention_reason", None)
        if reason:
            out["abstention_reason"] = reason
        examined = getattr(result, "examined", None)
        if examined:
            out["examined"] = list(examined)
        unexamined = getattr(result, "unexamined_tools", None)
        if unexamined:
            out["unexamined_tools"] = list(unexamined)
    return out


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
