"""Dynamic Graph Builder for Protocol Execution (Kleene Closure) - INTEGRATED.

FILE: snodo/engine/loop.py (Task 3.4 + 3.7 + 5.2 Integration)

Takes a compiled Protocol object and builds a LangGraph StateGraph dynamically.
NOW WIRED WITH REAL AGENTS:
- Execute node → calls BasicCoderAdapter → writes files via WorkspaceMCP
- Validate node → runs pre_execute validators (ShellMCP + LLM stubs)
- Post-validate node → runs post_execute validators (QualityValidator)
- Git commits via GitMCP
- Checkpointer for persistent agent memory (Task 5.2)

Phase-aware validation (Task 3.7):
- pre_execute validators run before execution (governance gate)
- post_execute validators run after execution (quality gate)

INV3 (non-overridable validation) is structural/emergent — no single site:
  token issuance (tokens.py) requires satisfied quorum → token gate (server.py)
  blocks mutation tools → validation cannot be bypassed.
"""

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from langgraph.graph import END, StateGraph

import snodo.predicates.scope  # noqa: F401 — registers predicates on import
import snodo.predicates.secrets  # noqa: F401
import snodo.predicates.tests  # noqa: F401
import snodo.validators  # noqa: F401 — registers validators on import

# Import real implementations
from snodo.coders import LiteLLMAdapter, MockAdapter
from snodo.compiler.models import Protocol, Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.engine.constraints import ConstraintEngine
from snodo.engine.policy import PolicyEvaluator
from snodo.engine.premise import find_stale_citations
from snodo.engine.state import (  # noqa: F401 — re-exported for existing imports
    LoopStage,
    LoopState,
    _branch_exists,
    _build_audit_results,
    _slugify,
    _task_branch_name,
)
from snodo.engine.validators import ValidatorRunner
from snodo.infrastructure.config import DEFAULT_MODEL
from snodo.infrastructure.tokens import TokenIssuer
from snodo.tools.git import GitMCP, resolve_base_branch
from snodo.tools.shell import ShellMCP
from snodo.tools.workspace import WorkspaceMCP
from snodo.validators.context import ValidatorContext

_logger = logging.getLogger(__name__)


from snodo.engine.nodes.context import ContextMixin  # noqa: E402
from snodo.engine.nodes.executor import ExecutorMixin  # noqa: E402
from snodo.engine.nodes.governance import GovernanceNodeMixin  # noqa: E402
from snodo.engine.nodes.state import SerdeMixin  # noqa: E402
from snodo.engine.nodes.validation import ValidationNodeMixin  # noqa: E402
from snodo.engine.nodes.writeback import WritebackMixin, _canonical_halt  # noqa: E402


def _resolve_model_for_role(config: dict, role: str, fallback: str) -> str:
    """Resolve the LLM model for a given role from snodo config.

    Roles: ``validator``, ``classifier``, or any key under ``llm``.
    Falls back to top-level ``model``, then *fallback*.
    """
    return (
        config.get("llm", {}).get(role, {}).get("model")
        or config.get("llm", {}).get(f"{role}_llm", {}).get("model")
        or config.get("model")
        or fallback
    )


def _attempt_written_files(loop_state: "LoopState") -> List[str]:
    """Return file paths written by the current attempt.

    Prefer metadata recorded by the executor when available. Fall back to
    artifact paths, filtering known engine bookkeeping markers.
    """
    recorded = loop_state.metadata.get("attempt_written_files")
    if isinstance(recorded, list) and recorded:
        return sorted({str(path) for path in recorded if str(path).strip()})

    files = []
    for artifact in loop_state.artifacts:
        if not isinstance(artifact, str):
            continue
        if artifact == "git_commit" or artifact.startswith("git_error:"):
            continue
        if artifact.startswith("code_generated_for_"):
            continue
        if artifact.strip():
            files.append(artifact)
    return sorted(set(files))


def _combine_attempt_provenance(
    prior_provenance: Optional[list],
    current_attempt: int,
    current_files: List[str],
) -> list:
    provenance = _normalize_attempt_provenance(prior_provenance)
    files = sorted({str(path) for path in current_files if str(path).strip()})
    if files:
        provenance.append({"attempt": current_attempt, "files": files})
    return _normalize_attempt_provenance(provenance)


def _verdict_signature(failures: list) -> tuple:
    """A canonical, order-independent signature of a failure list.

    Two lists with the same (validator_id, severity, justification) tuples in
    the same multiset produce the same signature.  Used to detect a repeated
    verdict across two recovery attempts (ADR 021).
    """
    def _canonical(f: dict) -> tuple:
        return (f.get("validator_id"), f.get("severity"), f.get("justification"))

    return tuple(sorted(_canonical(f) for f in failures))


def verdict_signature_from_results(results: list) -> tuple:
    """Canonical failure signature of a validator-result list.

    Only failures participate (warn / blocker); a pass is not a failure and its
    prose is not load-bearing.
    """
    return _verdict_signature([
        {
            "validator_id": getattr(r, "validator_id", ""),
            "severity": getattr(r, "severity", None),
            "justification": getattr(r, "justification", "") or "",
        }
        for r in results
        if getattr(r, "severity", None) in ("warn", "blocker")
    ])


def _normalize_attempt_provenance(provenance: Optional[list]) -> list:
    normalized = []
    for entry in provenance or []:
        attempt = entry.get("attempt", "?")
        files = entry.get("files") or []
        unique_files = sorted({str(path) for path in files if str(path).strip()})
        if unique_files:
            normalized.append({"attempt": attempt, "files": unique_files})
    return normalized


def _attempt_read_files(loop_state: "LoopState") -> Dict[str, List[str]]:
    """Return the paths the current attempt inspected, as ``{files, directories}``.

    Paths only — the executor records the coder's read-set, never its file
    contents, so a later recovery attempt reuses the map of where to look
    without ever inheriting a stale view of a file it must now fix (#157).
    """
    recorded = loop_state.metadata.get("attempt_read_files")
    if isinstance(recorded, dict):
        files = {str(p) for p in (recorded.get("files") or []) if str(p).strip()}
        dirs = {str(p) for p in (recorded.get("directories") or []) if str(p).strip()}
        return {"files": sorted(files), "directories": sorted(dirs)}
    return {"files": [], "directories": []}


def _normalize_attempt_reads(reads: Optional[list]) -> list:
    """Normalize accumulated per-attempt read history, dropping empty entries."""
    normalized = []
    for entry in reads or []:
        files = sorted({str(p) for p in (entry.get("files") or []) if str(p).strip()})
        dirs = sorted({str(p) for p in (entry.get("directories") or []) if str(p).strip()})
        if files or dirs:
            normalized.append(
                {"attempt": entry.get("attempt", "?"), "files": files, "directories": dirs}
            )
    return normalized


def _combine_attempt_reads(
    prior_reads: Optional[list],
    current_attempt: int,
    current_reads: Dict[str, List[str]],
) -> list:
    """Accumulate per-attempt inspection paths across a recovery chain.

    Mirrors :func:`_combine_attempt_provenance` but for reads, and carries only
    paths — never contents (see :func:`_attempt_read_files`).
    """
    history = _normalize_attempt_reads(prior_reads)
    files = sorted({str(p) for p in (current_reads.get("files") or []) if str(p).strip()})
    dirs = sorted({str(p) for p in (current_reads.get("directories") or []) if str(p).strip()})
    if files or dirs:
        history.append({"attempt": current_attempt, "files": files, "directories": dirs})
    return history


def _first_line(text: str) -> str:
    """Return the first non-empty line of *text*, or ``""``."""
    stripped = (text or "").strip()
    return stripped.splitlines()[0] if stripped else ""


def _normalize_settled(settled: Optional[list]) -> list:
    normalized = []
    for entry in settled or []:
        if not isinstance(entry, dict):
            continue
        validator_id = str(entry.get("validator_id") or "").strip()
        if not validator_id:
            continue
        normalized.append({
            "validator_id": validator_id,
            "justification": str(entry.get("justification") or "").strip(),
        })
    return normalized


def _settled_from_results(results: Optional[list]) -> list:
    """Return the verdicts that a judge positively asserted.

    Only a ``pass`` claims a positive — that the validator judged its remit and
    found it holding.  A ``warn`` or ``blocker`` asserts no per-criterion
    positive at all, so none is derived: ``cited_criteria`` is built by scraping
    criterion numbers from the justification (including a bare-number fallback),
    so it means "criteria the judge mentioned", not "criteria that failed", and
    a passing judge can cite criteria too.  Reading an uncited criterion as
    settled would turn a judge's silence into evidence and do so in the one
    section whose job is to be honest about what is established.  The warning
    judge's own prose — "four of five criteria hold and criterion two does not"
    — is carried verbatim instead, in the only party entitled to state it.

    A pass whose gate was skipped is excluded: it verified nothing, so it is
    not evidence (ADR 028).
    """
    settled = []
    for result in results or []:
        if getattr(result, "error", False):
            continue
        if getattr(result, "severity", None) != "pass":
            continue
        if getattr(result, "skipped", False):
            continue
        validator_id = getattr(result, "validator_id", None)
        if not validator_id:
            continue
        settled.append({
            "validator_id": validator_id,
            "justification": getattr(result, "justification", "") or "",
        })
    return settled


def _build_recovery_spec(
    original_spec: str,
    failures: list,
    attempt_provenance: Optional[list] = None,
    attempt_reads: Optional[list] = None,
    settled: Optional[list] = None,
) -> str:
    """Synthesise a recovery spec from the original intent + accumulated failures.

    The original intent is carried forward exactly once, unchanged, and is the
    OPERATIVE instruction: the task is to implement the intent, nothing more.
    Each failure is an entry dict of the form ``{"attempt", "validator_id",
    "severity", "justification"}`` carrying the attempt number that produced
    it, so the spec never wraps a previous recovery spec and the failure list
    accumulates instead of nesting (ADR 021). The justification is preserved
    verbatim — including the bounded stdout/stderr tail the validator captured.

    The failures are framed as DIAGNOSTIC EVIDENCE, not as a second mandate
    (Fixes #78). A recovery spec that says "fix all these failures" sets the
    coder's sense of scope from the accumulated failure list — which grows
    with every attempt — and invites broad exploration: observed twice, a
    first attempt reached submit_files at turn 16 while its recovery read
    essentially the whole repository across 48 turns. The intent is the scope
    anchor; the failures tell the coder what went wrong, not what to build.

    A recovery attempt is PROPORTIONATE to what failed, not a fresh run of the
    whole task. The spec therefore states three things the old framing left
    out: the prior attempt's work is already on disk in this worktree and must
    not be reproduced; the verdicts that already hold (``settled``) are named;
    and the coder's job is the remainder. The settled entries are framed as
    "already holds, and will be re-judged" — never "ignore the rest" — because
    validators judge the final state regardless, so a coder told the rest does
    not matter could still break it. The original intent is not truncated,
    replaced, or rewritten: it stays on record verbatim and authoritative.

    ``attempt_provenance`` identifies files earlier attempts wrote in the same
    cumulative worktree. It is framed as ownership context, not a rewrite
    request: the coder may remove a superseded file from its own earlier
    attempt, but must not churn a listed file merely because it appears there
    (Fixes #97).

    ``attempt_reads`` is the map of paths earlier attempts inspected (files
    read, directories listed). It is carried to cut the recovery attempt's
    cold start — it re-opens the previous search rather than exploring the
    whole repo again (Fixes #157). It is deliberately paths ONLY, never file
    contents: between attempts the tree has changed (the prior attempt wrote
    files, the worktree moved), so a cached version handed to the next coder
    would make a stale view authoritative — worse than re-reading. The coder is
    told these are hints about where to look and must open a file before editing
    it.
    """
    provenance = _normalize_attempt_provenance(attempt_provenance)
    reads = _normalize_attempt_reads(attempt_reads)
    settled_verdicts = _normalize_settled(settled)
    settled_ids = [entry["validator_id"] for entry in settled_verdicts]
    prior_attempt = max(
        (f.get("attempt", 0) for f in failures if isinstance(f, dict)),
        default=0,
    )

    opened = (
        "The task is the INTENT below. This is a recovery attempt, not a fresh "
        "task."
    )
    if settled_ids:
        opened += " It resumes from a partial success."

    lines = [
        opened,
        "",
        "INTENT (unchanged from the original task):",
        original_spec,
        "",
        "CONSTRAINTS:",
        "- Implement exactly the intent above. Do not expand the task beyond it.",
        "- The failures below are diagnostic evidence of what went wrong on "
        "earlier attempts. Use them to diagnose, but they do not change the "
        "task and do not widen its scope.",
        "- The prior attempt's work is already on disk in this worktree "
        "(committed on this task's branch). Inspect it before writing; do not "
        "re-derive or reproduce work that is already present.",
    ]

    if settled_ids:
        lines.append(
            "- The SETTLED verdicts below already hold as of the prior attempt. "
            "They will be re-judged on the final state, so do not break them — "
            "but you should not need to re-establish them. Your job is the "
            "remaining work named by the failures."
        )

    if provenance:
        lines.extend(
            [
                "- The provenance below is ownership context for the same "
                "recovery worktree, not a rewrite request. If an earlier "
                "attempt wrote a file that your current architecture supersedes, "
                "remove that orphan as part of completing the intent. If a listed "
                "file is still needed or already correct, leave it alone.",
            ]
        )

    if provenance:
        lines.append("")
        lines.append("PROVENANCE (files written by earlier attempts in this chain):")
        for entry in provenance:
            files = ", ".join(entry["files"])
            lines.append(f"- attempt {entry['attempt']}: {files}")

    if reads:
        lines.append("")
        lines.append(
            "PRIOR INSPECTION MAP (paths earlier attempts opened — NOT their contents):"
        )
        lines.append(
            "- These point at where the relevant code lived so you can go "
            "straight there instead of exploring the whole repository again. "
            "The tree has changed since: open a file before editing it and "
            "never assume you already know what a listed file contains."
        )
        for entry in reads:
            parts = []
            if entry["files"]:
                parts.append("files: " + ", ".join(entry["files"]))
            if entry["directories"]:
                parts.append("dirs: " + ", ".join(entry["directories"]))
            lines.append(f"- attempt {entry['attempt']}: " + "; ".join(parts))

    prior_failures = [
        f for f in failures
        if isinstance(f, dict) and f.get("attempt") == prior_attempt
    ]
    passed_ids = [e["validator_id"] for e in settled_verdicts]
    if prior_attempt:
        lines.append("")
        lines.append(f"PRIOR ATTEMPT (attempt {prior_attempt}) — result:")
        lines.append(
            "- Its work is committed in this worktree; begin from it rather "
            "than reproducing it."
        )
        if passed_ids:
            lines.append("- Validators that passed: " + ", ".join(passed_ids))
        if prior_failures:
            lines.append(
                "- The prior attempt's outstanding verdicts, in each judge's "
                "own words:"
            )
            for f in prior_failures:
                lines.append(
                    f"    - {f.get('validator_id', '?')} "
                    f"({f.get('severity', '?')}): {f.get('justification', '')}"
                )

    if settled_verdicts:
        lines.append("")
        lines.append(
            "SETTLED (verified as already holding by the prior attempt — will "
            "be re-judged on the final state):"
        )
        for entry in settled_verdicts:
            detail = _first_line(entry["justification"])
            suffix = f" — {detail}" if detail else ""
            lines.append(f"- {entry['validator_id']}: passed{suffix}")

    if failures:
        lines.append("")
        lines.append("FAILURES (evidence, accumulated across recovery attempts):")
        for f in failures:
            attempt = f.get("attempt", "?")
            lines.append(
                f"- [attempt {attempt}] {f['validator_id']} ({f['severity']}): "
                f"{f['justification']}"
            )

    return "\n".join(lines)


class GraphBuilder(GovernanceNodeMixin, ValidationNodeMixin, ExecutorMixin, SerdeMixin, WritebackMixin, ContextMixin):
    """Builds LangGraph StateGraph from Protocol specification.
    
    NOW WITH REAL MCP INTEGRATION (Task 3.4):
    - workspace_mcp: File operations
    - git_mcp: Version control
    - shell_mcp: Test execution
    - coder: Code generation (BasicCoderAdapter)
    """
    
    def __init__(
        self,
        protocol: Protocol,
        workspace_mcp: Optional[WorkspaceMCP] = None,
        git_mcp: Optional[GitMCP] = None,
        shell_mcp: Optional[ShellMCP] = None,
        coder: Optional[Union[LiteLLMAdapter, MockAdapter]] = None,
        checkpointer: Any = None,
        governance_fn: Optional[Callable[..., Any]] = None,
        validator_fn: Optional[Callable[..., List[ValidatorResult]]] = None,
        executor_fn: Optional[Callable[..., List[str]]] = None,
        audit_log: Any = None,
        session_manager: Any = None,
        token_issuer: Optional[TokenIssuer] = None,
        predicate_registry: Any = None,
        session_id: Optional[str] = None,
        validator_config: Any = None,
        project_root: Optional[str] = None,
        job_id: Optional[str] = None,
        worktree_path: Optional[str] = None,
        worktree_degraded: bool = False,
        verbose: bool = False,
        verdict_cache: Any = None,
        wave_results: Optional[dict] = None,
    ):
        """Initialize graph builder with real MCP services.

        Args:
            protocol: The protocol specification
            workspace_mcp: Workspace MCP for file operations
            git_mcp: Git MCP for version control
            shell_mcp: Shell MCP for test execution
            coder: Coder adapter for code generation
            checkpointer: LangGraph checkpointer for persistent memory (e.g., SqliteSaver)
            governance_fn: Optional custom governance checker
            validator_fn: Optional custom validator runner
            executor_fn: Optional custom executor
            audit_log: Optional AuditLog for INV4 event logging
            session_manager: Optional SessionManager for INV5 session state
            token_issuer: Optional TokenIssuer for JWT validation tokens (7.7)
            predicate_registry: Optional PredicateRegistry for constraint evaluation (7.8)
            session_id: Optional active session ID to tag on every audit event
            validator_config: Pre-loaded ValidatorConfig (cached at build time)
        """
        from snodo.coders.mock import (
            MockAdapter,
            is_mock_mode_active,
        )

        self.protocol = protocol
        self.workspace_mcp = workspace_mcp
        self.git_mcp = git_mcp
        self.shell_mcp = shell_mcp
        self.coder = coder or MockAdapter()
        # The progress callback is part of the DECLARED coder interface (base
        # class default on Coder), so it is assigned unconditionally — never
        # behind a hasattr guard (docs/architecture/coder-adapter-contract.md
        # §3.1, #68). An adapter that does not report progress inherits the
        # visible default rather than being silently skipped.
        self.coder.progress_callback = self._progress
        self.checkpointer = checkpointer
        self._audit_log = audit_log
        self._session_manager = session_manager
        # Ownership follows construction: an injected issuer is released by
        # the caller that created it; one constructed here belongs to the
        # builder and is released by close().
        self._owns_token_issuer = token_issuer is None
        self._token_issuer = token_issuer or TokenIssuer()
        self._session_id = session_id

        from snodo.predicates.registry import _default_registry
        self._predicate_registry = predicate_registry or _default_registry

        self._constraint_engine = ConstraintEngine(
            protocol=self.protocol,
            predicate_registry=self._predicate_registry,
            workspace_mcp=workspace_mcp,
            git_mcp=git_mcp,
        )

        _base_fn = getattr(self.coder, "_completion_fn", None) or \
                   getattr(self.coder, "completion_fn", None)

        self._completion_fn = _base_fn
        coder_model_fallback = getattr(self.coder, "model", DEFAULT_MODEL)

        from litellm import completion as litellm_completion
        from snodo.config import ConfigManager
        from snodo.validators.runner import build_completion_fn

        config = ConfigManager().load()

        validator_model = _resolve_model_for_role(config, "validator", coder_model_fallback)
        classifier_model = _resolve_model_for_role(config, "classifier", coder_model_fallback)

        if is_mock_mode_active() or isinstance(self.coder, MockAdapter):
            from snodo.coders.mock import mock_completion_fn
            mock_base = _base_fn or mock_completion_fn
            validator_completion_fn = build_completion_fn(validator_model, mock_base)
            classifier_completion_fn = build_completion_fn(classifier_model, mock_base)
        else:
            validator_completion_fn = build_completion_fn(validator_model, _base_fn or litellm_completion)
            classifier_completion_fn = build_completion_fn(classifier_model, _base_fn or litellm_completion)

        if classifier_model == validator_model and not (is_mock_mode_active() or isinstance(self.coder, MockAdapter)):
            classifier_completion_fn = validator_completion_fn

        self._classifier_completion_fn = classifier_completion_fn
        # The classifier model is resolved exactly once, here.  The same value
        # binds the completion function (model + api_base) and is passed to the
        # classification call, so the two can never disagree (ADR 020).
        self._classifier_model = classifier_model
        self._validator_model = validator_model
        self._default_model = validator_model
        self._validator_runner = ValidatorRunner(
            protocol=self.protocol,
            completion_fn=validator_completion_fn,
            default_model=validator_model,
            validator_config=validator_config,
            audit_log=self._audit_log,
            workspace_mcp=workspace_mcp,
            git_mcp=git_mcp,
            session_manager=session_manager,
        )
        self._validator_runner._session_id = self._session_id or ""
        # The verdict cache is an injected optimisation (#246): None means
        # judge fresh every time, exactly as before the cache existed.
        self._verdict_cache = verdict_cache
        self._validator_runner._verdict_cache = verdict_cache
        self._validator_runner._wave_results = wave_results

        self.governance_fn = governance_fn or self._default_governance
        self.validator_fn = validator_fn or self._validator_runner.run
        self.executor_fn = executor_fn or self._default_executor

        from snodo.infrastructure.decisions import (
            VerifyOnlyDecisionRecordIssuer,
        )
        from snodo.infrastructure.signing_keys import load_public_key
        self._decision_issuer = VerifyOnlyDecisionRecordIssuer(
            load_public_key(),
            audit_log=self._audit_log,
        )
        self.policy_evaluator = PolicyEvaluator(
            decision_issuer=self._decision_issuer,
        )
        self._summary_model = self._init_summary_model()
        self._project_root = project_root or ""
        self._job_id = job_id or ""
        self._worktree_path = worktree_path or ""
        self._worktree_degraded = worktree_degraded
        self._verbose = verbose
        #: Per-builder renderer so repeated turn lines compact across the whole
        #: run rather than within one emitter's lifetime (#294). Built here, not
        #: lazily, so validator-pool threads share the one instance.
        from snodo.engine.progress import ProgressRenderer
        self._progress_renderer: Optional[Any] = ProgressRenderer()
        self._project_context_cache: Optional[Dict[str, Any]] = None
        self._last_execution_writes: List[str] = []
        self._last_execution_reads: Dict[str, List[str]] = {"files": [], "directories": []}
        self._last_commit_reason: Optional[str] = None
        # Which coder binary (and version) produced the run, resolved per
        # dispatch by the adapter (Fixes #290).
        self._last_coder_binary: str = ""
        self._last_coder_version: str = ""
        self._last_existing_work_base_ref: Optional[str] = None
        self._last_output_tail: str = ""
        self._last_timed_out: bool = False
        self._last_timeout_seconds: Optional[int] = None
        self._last_timeout_tail: str = ""
        self._last_turn_budget_exhausted: bool = False
    
    def close(self) -> None:
        """Release resources this builder owns.

        Closes the ``TokenIssuer`` — and through it the store's SQLite
        connection — only when the builder constructed it.  An injected
        issuer belongs to the caller.  Idempotent.
        """
        if self._owns_token_issuer:
            self._token_issuer.close()

    def __enter__(self) -> "GraphBuilder":
        return self

    def __exit__(self, *exc: object) -> bool:
        self.close()
        return False

    def build_graph(self) -> StateGraph:
        """Build executable StateGraph from protocol.

        Graph flow:
          governance → validate(pre_execute) → execute → post_validate → move_next → complete
                                                ↑                          |
                                                blocked                  blocked
        """
        workflow = StateGraph(dict)  # type: ignore[type-var]

        # Add nodes
        workflow.add_node("governance", self._governance_node)  # type: ignore[type-var]
        workflow.add_node("validate", self._validate_node)  # type: ignore[type-var]
        workflow.add_node("execute", self._execute_node)  # type: ignore[type-var]
        workflow.add_node("post_validate", self._post_validate_node)  # type: ignore[type-var]
        workflow.add_node("move_next", self._move_next_node)  # type: ignore[type-var]
        workflow.add_node("blocked", self._blocked_node)  # type: ignore[type-var]
        workflow.add_node("complete", self._complete_node)  # type: ignore[type-var]
        workflow.add_node("recovery", self._recovery_node)  # type: ignore[type-var]

        # Set entry point
        workflow.set_entry_point("governance")

        # Add edges
        workflow.add_conditional_edges(
            "governance",
            self._route_after_governance,
            {
                "validate": "validate",
                "execute": "execute",
                "blocked": "blocked",
            }
        )
        workflow.add_conditional_edges(
            "validate",
            self._route_after_validation,
            {
                "execute": "execute",
                "governance": "governance",
                "blocked": "blocked"
            }
        )
        workflow.add_conditional_edges(
            "execute",
            self._route_after_execute,
            {
                "post_validate": "post_validate",
                "blocked": "blocked",
            }
        )
        workflow.add_conditional_edges(
            "post_validate",
            self._route_after_post_validation,
            {
                "move_next": "move_next",
                "blocked": "blocked",
                "recovery": "recovery",
            }
        )
        workflow.add_edge("recovery", END)
        workflow.add_conditional_edges(
            "move_next",
            self._route_after_move,
            {
                "governance": "governance",
                "complete": "complete"
            }
        )
        workflow.add_edge("blocked", END)
        workflow.add_edge("complete", END)

        return workflow

    def _is_recoverable(self, loop_state: LoopState, results: list) -> bool:
        """Determine whether a HALT outcome is recoverable (overridable).

        Does NOT check depth bounds — that is handled by
        _spawn_recovery_subtask which distinguishes within-budget
        vs recovery_exhausted.
        """
        # Non-error blockers from validators WITHOUT severity_cap are structural
        for r in results:
            if r.severity == "blocker" and not getattr(r, 'error', False):
                v = self._find_validator(r.validator_id)
                if v is not None and v.severity_cap is None:
                    return False
        return True

    def _stale_recovery_citations(self, root_spec: str) -> list:
        """Presence claims in *root_spec* the worktree no longer satisfies.

        Resolves the tree the recovery coder would run in — the workspace root,
        then the worktree, then the project root.  When none is known (a bare
        unit construction) the check is skipped: no root means no evidence, and
        a false stale verdict is worse than no check (Fixes #286).
        """
        root = None
        workspace_root = getattr(self.workspace_mcp, "project_root", None)
        if workspace_root is not None:
            root = Path(workspace_root)
        elif self._worktree_path:
            root = Path(self._worktree_path)
        elif self._project_root:
            root = Path(self._project_root)
        if root is None:
            return []
        return find_stale_citations(root_spec, root)

    def _spawn_recovery_subtask(self, loop_state: LoopState, results: list, decision: Any) -> None:
        """Spawn a recovery subtask or mark recovery_exhausted if at depth cap.

        The subtask derives from the ROOT task, not the previous attempt: its id
        is ``<root>_fix_N`` (linearly numbered by depth) and its spec carries the
        original intent once plus the accumulated failure list.  A repeated
        verdict halts the loop before depth is exhausted (ADR 021).

        The spec is not just the original task with failures appended: it also
        names the verdicts that already hold and states that the prior attempt's
        work is on disk, so a recovery attempt is proportionate to the remainder
        rather than a re-run of the whole task.  The original intent is still
        carried verbatim and stays authoritative.
        """
        current_depth = loop_state.task.depth or 0
        max_depth = self.protocol.max_recovery_depth_for(loop_state.current_mode)

        if current_depth >= max_depth:
            loop_state.is_blocked = True
            loop_state.halt_type = "recovery_exhausted"
            loop_state.constraint_violations.append(
                f"Recovery depth exhausted (depth={current_depth}, max={max_depth})"
            )
            self._progress(
                f"  Recovery depth exhausted (depth {current_depth}/{max_depth}): limit reached; halting loop"
            )
            self._audit("recovery_exhausted", {
                "op": "recovery_exhausted",
                "task_ref": loop_state.task.id,
                "depth": current_depth,
                "max_depth": max_depth,
            })
            return

        # The root of this recovery chain: the original task id and intent.
        root_id = loop_state.task.root_task_ref or loop_state.task.id
        root_spec = loop_state.task.root_spec or loop_state.task.spec

        # Failures produced by THIS attempt, tagged with the 1-based attempt
        # number (root = 1, fix_1 = 2, ...).
        attempt_no = current_depth + 1
        new_failures = [
            {
                "attempt": attempt_no,
                "validator_id": r.validator_id,
                "severity": r.severity,
                "justification": r.justification,
            }
            for r in results
            if r.severity in ("warn", "blocker")
        ]

        # A spec that asserts evidence the tree no longer holds is stale: an
        # earlier attempt fixed the very thing it cites, so the next coder
        # would be sent to find a defect that is gone (Fixes #286).  The engine
        # reports the stale premise and refuses to dispatch; it does not rewrite
        # the spec, because deciding what the task now means is not its call
        # (#35).  The check is skipped when no worktree root is known.
        stale = self._stale_recovery_citations(root_spec)
        if stale:
            details = "; ".join(c.describe() for c in stale)
            loop_state.is_blocked = True
            loop_state.halt_type = "escalated"
            loop_state.constraint_violations.append(
                "Recovery premise is stale: the spec asserts evidence the tree "
                f"no longer contains ({details}). The engine does not rewrite "
                "the spec; re-scope the task or retry with a corrected spec."
            )
            self._progress(
                f"  Recovery premise stale (attempt {attempt_no}/{max_depth}): "
                f"{details}; halting instead of dispatching"
            )
            self._audit("spec_premise_stale", {
                "op": "spec_premise_stale",
                "task_ref": loop_state.task.id,
                "depth": current_depth,
                "citations": [
                    {"path": c.path, "construct": c.construct, "line": c.line}
                    for c in stale
                ],
            })
            return

        # Identical repeated verdict: this attempt's failures match the previous
        # attempt's, so the loop cannot converge.  Stop before spending another
        # coder call plus a full quorum (ADR 021).
        previous = [
            f for f in (loop_state.task.prior_failures or [])
            if f.get("attempt") == current_depth
        ]
        if new_failures and _verdict_signature(previous) == _verdict_signature(new_failures):
            loop_state.is_blocked = True
            loop_state.halt_type = "recovery_stalled"
            loop_state.constraint_violations.append(
                "Recovery stalled: this attempt produced the same validator "
                "verdict as the previous attempt; the loop cannot converge."
            )
            self._progress(
                f"  Recovery stalled (attempt {current_depth + 1}/{max_depth}): identical validator verdict as previous attempt; halting loop"
            )
            self._audit("recovery_stalled", {
                "op": "recovery_stalled",
                "task_ref": loop_state.task.id,
                "depth": current_depth,
                "validator_ids": [f["validator_id"] for f in new_failures],
            })
            return

        # Accumulate failures across attempts rather than replacing them.
        accumulated = list(loop_state.task.prior_failures or []) + new_failures
        provenance = _combine_attempt_provenance(
            loop_state.task.attempt_provenance,
            attempt_no,
            _attempt_written_files(loop_state),
        )
        read_history = _combine_attempt_reads(
            loop_state.task.attempt_reads,
            attempt_no,
            _attempt_read_files(loop_state),
        )
        # Verdicts a judge positively asserted, drawn from every validator that
        # ran this attempt (pre- and post-execute). Only a pass claims a
        # positive; a warning judge's own prose stays in the failure evidence.
        # These tell the recovery coder what it need not re-establish; they are
        # re-judged on the final state anyway.
        settled = _settled_from_results(loop_state.validation_results or results)
        spec = _build_recovery_spec(
            root_spec, accumulated, provenance, read_history, settled
        )

        # Identify triggering validators (warn / blocker)
        trigger_ids = [f["validator_id"] for f in new_failures]

        fix_number = current_depth + 1
        fix_task = Task(
            id=f"{root_id}_fix_{fix_number}",
            spec=spec,
            module_id=loop_state.task.module_id,
            parent_task_ref=loop_state.task.id,
            root_task_ref=root_id,
            root_spec=root_spec,
            prior_failures=accumulated,
            attempt_provenance=provenance,
            attempt_reads=read_history,
            depth=current_depth + 1,
        )
        loop_state.spawned_subtasks.append(fix_task)
        loop_state.needs_recovery = True

        triggers_str = ", ".join(f"{f['validator_id']} ({f['severity']})" for f in new_failures) or "validation failure"
        self._progress(
            f"  Recovery (attempt {fix_task.depth}/{max_depth}): spawned {fix_task.id} ({triggers_str})"
        )

        self._audit("subtask_spawned", {
            "op": "subtask_spawned",
            "parent_ref": loop_state.task.id,
            "task_ref": fix_task.id,
            "depth": fix_task.depth,
            "triggering_validator_ids": trigger_ids,
        })

    def _find_validator(self, validator_id: str):
        """Look up a validator spec by ID from the protocol."""
        return self.protocol.get_validator(validator_id)

    def _judges_spec(self, validator_id: str) -> bool:
        """Return True if a validator's critique is about the spec, not the work.

        A validator whose critique is spec-quality (wording, intent, constraints,
        scope) may feed the spec-authoring rewriter.  A non-spec objection is
        about the work and must not silently reshape the spec (Fixes #35).
        Unknown validators default to False so an unmarked protocol cannot
        accidentally launder work critique into the spec.
        """
        v = self._find_validator(validator_id)
        return bool(v is not None and getattr(v, "judges_spec", False))

    def _move_next_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Stage 4: Move to next task or complete."""
        loop_state = self._dict_to_state(state)
        loop_state.stage = LoopStage.MOVE_NEXT

        # Simple completion logic
        loop_state.is_complete = True

        self._audit("transition", {
            "op": "transition",
            "task_ref": loop_state.task.id,
            "from_mode": loop_state.current_mode,
            "to_mode": "complete",
        })

        return self._state_to_dict(loop_state)
    
    def _blocked_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Terminal node: Blocker encountered."""
        loop_state = self._dict_to_state(state)

        # Log halt BEFORE entering blocked state
        blocker_validators = [
            r.validator_id for r in loop_state.validation_results
            if r.severity == "blocker"
        ]
        canonical_halt = _canonical_halt(loop_state.halt_type)
        raw_halt = loop_state.halt_type or canonical_halt
        halt_audit = {
            "op": "halt",
            "task_ref": loop_state.task.id,
            "reason": "; ".join(loop_state.constraint_violations) or "blocker",
            "blocker_validators": blocker_validators,
            "halt_type": canonical_halt,
            "raw_halt_type": raw_halt,
        }
        if loop_state.metadata.get("timed_out"):
            halt_audit["timed_out"] = True
            halt_audit["timeout_seconds"] = loop_state.metadata.get("timeout_seconds")
        self._audit("halt", halt_audit)

        loop_state.stage = LoopStage.BLOCKED
        self._auto_write_halt_payload(loop_state)
        return self._state_to_dict(loop_state)
    
    def _task_change_size(self) -> Optional[Dict[str, Any]]:
        """How much the task's branch changed, against the point it branched from.

        The size — line totals and per-shape file counts — never the diff:
        the record answers "was this night's work substantial?" without
        shipping content off the machine. The base is the merge-base of the
        resolved base branch and the task branch (the same branch and base
        the merge path resolves), so a long-running task reports its own
        work, not everything anyone else merged while it ran.

        ``None`` is the honest absence of a measurement — no git, no task
        branch, an unresolvable base — never a fabricated zero. A degraded
        or non-isolated run sits on the operator's own branch, which the
        task-branch guard reads as unmeasurable. The completion record is
        an observer: a git failure here must not break the run, so it
        returns None and says so in the log (Fixes #377).
        """
        git = self.git_mcp
        if git is None:
            return None
        try:
            branch = git.repo.active_branch.name
        except Exception:
            return None
        if not self._active_branch_is_task_branch(branch):
            return None
        try:
            head = git.repo.head.commit
            base_tip = git.repo.commit(resolve_base_branch(str(git.project_root)))
            merge_bases = git.repo.merge_base(base_tip, head)
            if not merge_bases:
                return None
            return git.change_size(merge_bases[0].hexsha, head.hexsha)
        except Exception as e:
            _logger.debug("Could not record change size for %s: %s", branch, e)
            return None

    def _complete_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Terminal node: Work complete."""
        loop_state = self._dict_to_state(state)
        loop_state.stage = LoopStage.COMPLETE

        self._clear_failure_context(loop_state)

        change_size = self._task_change_size()
        loop_state.metadata["change_size"] = change_size
        task_complete_audit = {
            "op": "task_complete",
            "task_ref": loop_state.task.id,
            "artifacts": loop_state.artifacts,
            # Explicit null distinguishes a completed no-op from an old event
            # that predates commit provenance.
            "commit": loop_state.metadata.get("commit"),
            "change_size": change_size,
        }
        if loop_state.metadata.get("timed_out"):
            task_complete_audit["timed_out"] = True
            task_complete_audit["timeout_seconds"] = loop_state.metadata.get("timeout_seconds")
        self._audit("task_complete", task_complete_audit)

        loop_state.messages.append({
            "role": "assistant",
            "content": f"Task completed successfully. "
                       f"Iterations: {loop_state.iteration}. "
                       f"Artifacts: {len(loop_state.artifacts)}."
        })
        self._auto_write_halt_payload(loop_state)
        return self._state_to_dict(loop_state)

    def _recovery_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Terminal node: Records spawned recovery subtask.

        Returns control to the outer driver (K2) which will invoke
        the graph again for each spawned subtask. Does NOT set
        is_blocked — the subtask should be picked up for execution.
        """
        loop_state = self._dict_to_state(state)
        loop_state.stage = LoopStage.MOVE_NEXT
        self._auto_write_halt_payload(loop_state)
        return self._state_to_dict(loop_state)

    def _route_after_execute(self, state: Dict[str, Any]) -> str:
        """Route after execution: post-validate on success, block on failure.

        A failed execution must never reach post-validation — validating the
        unchanged worktree would produce a green verdict on zero artifacts.
        """
        loop_state = self._dict_to_state(state)
        if loop_state.is_blocked:
            return "blocked"
        return "post_validate"

    def _route_after_move(self, state: Dict[str, Any]) -> str:
        """Route after move_next based on completion."""
        loop_state = self._dict_to_state(state)
        
        if loop_state.is_complete:
            return "complete"
        else:
            return "governance"
    
    def _route_after_governance(self, state: Dict[str, Any]) -> str:
        """Route after governance: proceed, block, or skip validation."""
        loop_state = self._dict_to_state(state)
        if loop_state.is_blocked:
            return "blocked"
        return "validate"

    def _default_governance(self, state: LoopState, protocol: Protocol) -> LoopState:
        """Evaluate protocol and mode constraints against execution context."""
        return self._constraint_engine.evaluate(state, "governance", self._audit)

    def _default_validator(
        self,
        task: Task,
        validators: List[Validator],
        shell_mcp: Optional[ShellMCP],
        current_mode: str = "",
        phase: str = "",
        artifacts: Optional[List[str]] = None,
        base_ref: Optional[str] = None,
    ) -> List[ValidatorResult]:
        """Validator dispatch via the shared runner (single implementation).

        Delegates to ``run_validators`` so severity-capping (and its
        error-flag guard) lives in exactly one place.  ``dispatch_fn`` is
        bound to ``self._dispatch_one`` so tests can monkey-patch it and have
        it take effect.
        """
        from snodo.validators.runner import run_validators

        results, cap_originals = run_validators(
            protocol=self.protocol,
            validators=validators,
            task=task,
            phase=phase,
            completion_fn=self._get_completion_fn(),
            default_model=getattr(self.coder, "model", DEFAULT_MODEL),
            validator_config=self._validator_runner._validator_config,
            workspace_mcp=self.workspace_mcp,
            git_mcp=self.git_mcp,
            current_mode=current_mode,
            session_id=self._session_id or "",
            audit_log=self._audit_log,
            dispatch_fn=self._dispatch_one,
            progress_cb=self._progress,
            verdict_cb=self._validator_verdict_cb,
            artifacts=artifacts,
            base_ref=base_ref,
            verdict_cache=self._verdict_cache,
        )
        self._validator_runner.last_cap_originals = cap_originals
        return results

    def _get_completion_fn(self):
        """Return the coder's completion function."""
        return self._completion_fn

    def _dispatch_one(
        self, v: Validator, context: ValidatorContext, reg
    ) -> ValidatorResult:
        """Delegate to ValidatorRunner."""
        return self._validator_runner._dispatch_one(v, context, reg)

    def _resolve_validators(
        self, mode_id: str, phase: str = "pre_execute"
    ) -> tuple:
        """Delegate to ValidatorRunner."""
        return self._validator_runner.resolve_validators(mode_id, phase)

    def _audit(self, event_type: str, data: Dict[str, Any]) -> None:
        """Log an audit event if audit_log is available."""
        if self._audit_log is not None:
            if self._session_id:
                data["session_id"] = self._session_id
            self._audit_log.append_event(event_type, data)

    def _progress(self, message: str, verbose: bool = False) -> None:
        """Print a progress line to stdout, styled for an interactive terminal.

        Normal-path transitions are always printed; per-validator verdicts and
        other fine-grained detail are gated behind ``verbose``.

        Presentation is decoration over the lines the engine already emits
        (#294): the renderer colours and compacts only on a tty, and writes the
        line verbatim otherwise, so a piped or redirected stream and a
        ``NO_COLOR`` caller see byte-identical output. What is emitted, when,
        and what reaches the audit log are unchanged.
        """
        if verbose and not self._verbose:
            return
        renderer = getattr(self, "_progress_renderer", None)
        if renderer is None:
            from snodo.engine.progress import ProgressRenderer
            renderer = ProgressRenderer()
            self._progress_renderer = renderer
        renderer(message)

    def _validator_verdict_cb(self, validator_id: str, result: Any) -> None:
        """Print a per-validator verdict as it lands (warn/blocker/error always; pass in verbose).

        Reports the *stored* (post-cap) severity so this line never contradicts
        the audit record; when a severity_cap was applied the pre-cap value is
        shown alongside it rather than hidden.
        """
        severity = getattr(result, "severity", "?")
        justification = getattr(result, "justification", "") or ""
        first_line = justification.strip().splitlines()[0] if justification.strip() else ""
        if len(first_line) > 80:
            first_line = first_line[:77] + "..."

        original = getattr(result, "severity_original", None)
        cap_note = f" (from {original})" if original and original != severity else ""
        reuse_note = " (reused)" if getattr(result, "reused", False) else ""

        if getattr(result, "error", False):
            snippet = f" — {first_line}" if first_line else ""
            self._progress(f"    💥 {validator_id}: error ({severity}){cap_note}{snippet}")
        elif severity == "pass" and not getattr(result, "skipped", False):
            self._progress(f"    ✓ {validator_id}: pass{cap_note}{reuse_note}", verbose=True)
        elif severity == "pass":
            # A pass that skipped its gate (e.g. the quality validator ran the
            # no-op default because no test command is configured) is visible
            # in normal output — an ungated project must never look like a
            # silently tested one.
            snippet = f" — {first_line}" if first_line else ""
            self._progress(f"    ✓ {validator_id}: pass (skipped){cap_note}{snippet}")
        elif severity == "warn":
            snippet = f" — {first_line}" if first_line else ""
            self._progress(f"    ⚠️ {validator_id}: warn{cap_note}{reuse_note}{snippet}")
        elif severity == "blocker":
            snippet = f" — {first_line}" if first_line else ""
            self._progress(f"    ❌ {validator_id}: blocker{cap_note}{reuse_note}{snippet}")
        else:
            snippet = f" — {first_line}" if first_line else ""
            self._progress(f"    💥 {validator_id}: {severity}{cap_note}{reuse_note}{snippet}")


def build_protocol_graph(
    protocol: Protocol,
    project_root: Optional[str] = None,
    use_mock_coder: bool = False,
    model: Optional[str] = None,
    coder: Optional[Any] = None,
    coder_name: Optional[str] = None,
    workspace_mcp: Optional[Any] = None,
    git_mcp: Optional[Any] = None,
    shell_mcp: Optional[Any] = None,
    checkpointer=None,
    audit_log: Any = None,
    session_manager: Any = None,
    session_id: Optional[str] = None,
    job_id: Optional[str] = None,
    worktree_path: Optional[str] = None,
    worktree_degraded: bool = False,
    verbose: bool = False,
    token_issuer: Optional[TokenIssuer] = None,
    verdict_cache: Any = None,
    wave_results: Optional[dict] = None,
    **custom_functions
) -> StateGraph:
    """Convenience function to build graph with MCP integration.

    Args:
        protocol: Protocol specification
        project_root: Project root for MCP services (defaults to current directory)
        use_mock_coder: If True, use MockCoderAdapter instead of real LLM
        model: Model identifier for the coder (default: claude-sonnet-4-20250514)
        coder: Pre-built coder adapter. When supplied it is used as-is and
            ``model``/``use_mock_coder`` are ignored — injection point for tests.
        coder_name: Registered coder backend name (e.g. "litellm", "opencode-cli")
        checkpointer: LangGraph checkpointer for persistent agent memory
        audit_log: Optional AuditLog for INV4 event logging
        session_manager: Optional SessionManager for INV5 session state
        session_id: Optional active session ID to tag on every audit event
        job_id: Job identifier for direct job state.json writes
        worktree_path: When set, MCPs root at the worktree instead of project_root
        worktree_degraded: Worktree creation failed — skip branch ops
        verbose: Print per-validator verdicts and fine-grained progress
        token_issuer: Optional caller-owned TokenIssuer. When supplied, the
            graph uses it and does NOT close it — the caller releases it.
        **custom_functions: Optional overrides

    Returns:
        Executable StateGraph with real MCP integration
    """
    if project_root is None:
        from snodo.infrastructure.paths import resolve_project_root
        project_root = str(resolve_project_root() or Path.cwd())

    # Use worktree as the working root when isolating tasks
    mcp_root = worktree_path or project_root

    # Initialize MCP services if not supplied
    if workspace_mcp is None:
        workspace_mcp = WorkspaceMCP(mcp_root)
    if git_mcp is None:
        try:
            git_mcp = GitMCP(mcp_root)
        except Exception:
            git_mcp = None
    if shell_mcp is None:
        shell_mcp = ShellMCP(mcp_root)

    from snodo.coders import get_coder, resolve_coder_name
    from snodo.coders.inert_settings import (
        explicit_coder_settings,
        report_inert_coder_settings,
    )
    from snodo.infrastructure.config import load_llm_config
    llm_cfg = load_llm_config()

    # Initialize coder with LLM config knobs if not passed directly
    if coder is None:
        initial_mode_obj = protocol.get_mode(protocol.initial_mode)
        mode_coder = getattr(initial_mode_obj, "coder", None) if initial_mode_obj else None
        mode_coder_config = getattr(initial_mode_obj, "coder_config", {}) if initial_mode_obj else {}
        resolved_model = (
            model
            or (mode_coder_config.get("model") if mode_coder_config else None)
            or llm_cfg.coder.model
            or DEFAULT_MODEL
        )
        resolved_name = resolve_coder_name(
            model=resolved_model,
            mode_coder=mode_coder,
            cli_coder=coder_name,
            use_mock=use_mock_coder,
        )
        coder_kwargs: Dict[str, Any] = {
            "max_tokens": llm_cfg.coder.max_tokens,
            "max_tool_turns": llm_cfg.coder.max_tool_turns,
            "timeout_seconds": llm_cfg.coder.timeout_seconds,
            "workspace_mcp": workspace_mcp,
        }
        if mode_coder_config:
            coder_kwargs.update(mode_coder_config)
        # The pairing of coder and settings is decided HERE, so an explicitly
        # configured setting the chosen coder cannot honour is named now, not
        # after a run has shown it had no effect (Fixes #311).
        report_inert_coder_settings(
            resolved_name,
            explicit_coder_settings(llm_cfg.coder, mode_coder_config),
        )
        coder = get_coder(
            resolved_name,
            model=resolved_model,
            **coder_kwargs,
        )

    if isinstance(wave_results, str):
        import json
        try:
            wave_results = json.loads(wave_results)
        except (TypeError, ValueError):
            wave_results = None

    custom_functions.pop("workspace_mcp", None)
    custom_functions.pop("git_mcp", None)
    custom_functions.pop("shell_mcp", None)
    custom_functions.pop("coder", None)

    builder = GraphBuilder(
        protocol,
        workspace_mcp=workspace_mcp,
        git_mcp=git_mcp,
        shell_mcp=shell_mcp,
        coder=coder,
        checkpointer=checkpointer,
        audit_log=audit_log,
        session_manager=session_manager,
        session_id=session_id,
        validator_config=llm_cfg.validator,
        project_root=project_root,
        job_id=job_id,
        worktree_path=worktree_path,
        worktree_degraded=worktree_degraded,
        verbose=verbose,
        token_issuer=token_issuer,
        verdict_cache=verdict_cache,
        wave_results=wave_results,
        **custom_functions
    )
    return builder.build_graph()
