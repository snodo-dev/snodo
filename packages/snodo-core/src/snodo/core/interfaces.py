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
    #: Best-effort coder report from the last implementation run.
    last_report: Optional[Any] = None
    #: Substantive findings from the last implementation run (for non-diff tasks).
    last_findings: Optional[Any] = None

    @classmethod
    def availability_requirements(cls) -> tuple:
        """Programs this coder needs invokable before a task may be dispatched.

        Pairs of ``(binary, remediation)``, where *remediation* is the
        operator-facing install command shown when *binary* is not on PATH.
        Declared on the ABC (empty default) so every adapter states its own
        environment needs and a dispatcher can check them with one
        ``shutil.which`` call — in the process that will actually invoke the
        coder, which is not the shell where readiness ran. A pure-API coder
        inherits the empty default: nothing to install, nothing to check.
        """
        return ()

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
    module_id: Optional[str] = None
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
    #: The module this task is scoped to (ADR 041), if any. A declared module
    #: bounds what the task may write; an absent one leaves the protocol's own
    #: scope in force. Reads are never bounded by it.
    module_id: Optional[str] = None


class ValidatorResult(BaseModel):
    """Output from a single validator.

    A validator returns a verdict. ``severity`` is required: there is no
    fourth thing a validator can return. A judge that cannot reach a verdict
    is an operational error (``error=True``, severity ``blocker``) and fails
    closed like every other error, rather than inventing a state to describe
    a verdict nobody gave.
    """
    validator_id: str
    severity: Literal["pass", "warn", "blocker"]
    justification: str
    error: bool = False
    cited_criteria: Optional[List[str]] = None
    #: Set by a judge when a criterion needs a capability it was not granted.
    tool_access_missing: Optional[Dict[str, str]] = None
    #: Pre-cap severity when a severity_cap downgraded this result; None otherwise.
    severity_original: Optional[str] = None
    #: True when the validator's gate was skipped rather than genuinely
    #: exercised (e.g. the quality validator ran the no-op default because no
    #: test command is configured). Severity stays "pass" so the task proceeds,
    #: but a skipped result is surfaced in normal run output and must never be
    #: mistaken for real verification evidence.
    skipped: bool = False
    #: What a judge that failed operationally examined before it failed — an
    #: ordered summary of the tool calls it made ("turn 3: read_file
    #: src/auth.py"). Carried on the error result so a human can see how far a
    #: failing inspection got, without inventing a state to hold it.
    examined: Optional[List[str]] = None
    #: True when this verdict was served from the project's verdict cache
    #: rather than freshly judged (#246).  The verdict is still that
    #: validator's verdict and counts toward the quorum as such; the flag
    #: exists only so the operator's view and the audit trail can say the
    #: judgement was reused instead of presenting it as newly made.  An error
    #: and a skipped pass are never stored or reused.
    reused: bool = False
    #: False when the provider forced a non-deterministic parameter fallback.
    cacheable: bool = True

    def record(self) -> Dict[str, Any]:
        """The canonical audit/display record for this result. See result_record."""
        return result_record(self)


def result_record(result: Any) -> Dict[str, Any]:
    """The canonical audit/display record for a validator result.

    Every site that records, serialises or displays what a validator concluded
    goes through this one function.  Attribute-based so faithful test doubles
    of ValidatorResult serialise identically to real instances.
    """
    out: Dict[str, Any] = {
        "validator_id": getattr(result, "validator_id", ""),
        "severity": getattr(result, "severity", None),
        "justification": getattr(result, "justification", ""),
    }
    # What a failing judge examined travels with the failure, so the record of
    # a partial inspection is kept without a state to hold a missing verdict.
    examined = getattr(result, "examined", None)
    if examined:
        out["examined"] = list(examined)
    # A reused verdict is a real verdict; the flag marks how it was obtained
    # so an audit reader can tell a reused judgement from a fresh one (#246).
    if getattr(result, "reused", False):
        out["reused"] = True
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
    findings: Optional[Any] = None
