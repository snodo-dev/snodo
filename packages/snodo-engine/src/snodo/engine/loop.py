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
from snodo.tools.git import GitMCP
from snodo.tools.shell import ShellMCP
from snodo.tools.workspace import WorkspaceMCP
from snodo.validators.context import ValidatorContext


from snodo.engine.nodes.context import ContextMixin  # noqa: E402
from snodo.engine.nodes.executor import ExecutorMixin  # noqa: E402
from snodo.engine.nodes.governance import GovernanceNodeMixin  # noqa: E402
from snodo.engine.nodes.state import SerdeMixin  # noqa: E402
from snodo.engine.nodes.validation import ValidationNodeMixin  # noqa: E402
from snodo.engine.nodes.writeback import WritebackMixin  # noqa: E402


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


def _build_completion_fn(model: str, base_fn: Optional[Callable]) -> Optional[Callable]:
    """Build a ``functools.partial`` of *base_fn* bound to *model*.

    If the model's provider has a ``base_url`` configured, ``api_base``
    is also bound so the call routes to the correct endpoint.
    """
    if base_fn is None:
        return None

    import functools

    from snodo.config import ConfigManager
    resolver = getattr(ConfigManager, "resolve_litellm_model", None)
    litellm_model = resolver(model) if resolver else model
    kwargs: dict[str, Any] = {"model": litellm_model}
    api_base = ConfigManager.resolve_api_base(model)
    if api_base:
        kwargs["api_base"] = api_base
    return functools.partial(base_fn, **kwargs)


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
    return tuple(sorted(
        (f.get("validator_id"), f.get("severity"), f.get("justification"))
        for f in failures
    ))


def _normalize_attempt_provenance(provenance: Optional[list]) -> list:
    normalized = []
    for entry in provenance or []:
        attempt = entry.get("attempt", "?")
        files = entry.get("files") or []
        unique_files = sorted({str(path) for path in files if str(path).strip()})
        if unique_files:
            normalized.append({"attempt": attempt, "files": unique_files})
    return normalized


def _build_recovery_spec(
    original_spec: str,
    failures: list,
    attempt_provenance: Optional[list] = None,
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

    ``attempt_provenance`` identifies files earlier attempts wrote in the same
    cumulative worktree. It is framed as ownership context, not a rewrite
    request: the coder may remove a superseded file from its own earlier
    attempt, but must not churn a listed file merely because it appears there
    (Fixes #97).
    """
    provenance = _normalize_attempt_provenance(attempt_provenance)
    lines = [
        "The task is the INTENT below. Implement it.",
        "",
        "INTENT (unchanged from the original task):",
        original_spec,
        "",
        "CONSTRAINTS:",
        "- Implement exactly the intent above. Do not expand the task beyond it.",
        "- The failures below are diagnostic evidence of what went wrong on "
        "earlier attempts. Use them to diagnose, but they do not change the "
        "task and do not widen its scope.",
    ]

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
        self._default_model = getattr(self.coder, "model", DEFAULT_MODEL)

        from litellm import completion as litellm_completion
        from snodo.config import ConfigManager, provider_env

        config = ConfigManager().load()

        validator_model = _resolve_model_for_role(config, "validator", self._default_model)
        classifier_model = _resolve_model_for_role(config, "classifier", self._default_model)

        if is_mock_mode_active() or isinstance(self.coder, MockAdapter):
            from snodo.coders.mock import mock_completion_fn
            mock_base = _base_fn or mock_completion_fn
            validator_completion_fn = _build_completion_fn(validator_model, mock_base)
            classifier_completion_fn = _build_completion_fn(classifier_model, mock_base)
        else:
            with provider_env(validator_model), provider_env(classifier_model):
                validator_completion_fn = _build_completion_fn(validator_model, _base_fn or litellm_completion)
                classifier_completion_fn = _build_completion_fn(classifier_model, _base_fn or litellm_completion)

        if classifier_model == validator_model and not (is_mock_mode_active() or isinstance(self.coder, MockAdapter)):
            classifier_completion_fn = validator_completion_fn

        self._classifier_completion_fn = classifier_completion_fn
        # The classifier model is resolved exactly once, here.  The same value
        # binds the completion function (model + api_base) and is passed to the
        # classification call, so the two can never disagree (ADR 020).
        self._classifier_model = classifier_model
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
        self._project_context_cache: Optional[Dict[str, Any]] = None
        self._last_execution_writes: List[str] = []
        self._last_commit_reason: Optional[str] = None
    
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

    def _spawn_recovery_subtask(self, loop_state: LoopState, results: list, decision: Any) -> None:
        """Spawn a recovery subtask or mark recovery_exhausted if at depth cap.

        The subtask derives from the ROOT task, not the previous attempt: its id
        is ``<root>_fix_N`` (linearly numbered by depth) and its spec carries the
        original intent once plus the accumulated failure list.  A repeated
        verdict halts the loop before depth is exhausted (ADR 021).
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
        spec = _build_recovery_spec(root_spec, accumulated, provenance)

        # Identify triggering validators (warn / blocker)
        trigger_ids = [f["validator_id"] for f in new_failures]

        fix_number = current_depth + 1
        fix_task = Task(
            id=f"{root_id}_fix_{fix_number}",
            spec=spec,
            parent_task_ref=loop_state.task.id,
            root_task_ref=root_id,
            root_spec=root_spec,
            prior_failures=accumulated,
            attempt_provenance=provenance,
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
        self._audit("halt", {
            "op": "halt",
            "task_ref": loop_state.task.id,
            "reason": "; ".join(loop_state.constraint_violations) or "blocker",
            "blocker_validators": blocker_validators,
        })

        loop_state.stage = LoopStage.BLOCKED
        self._auto_write_halt_payload(loop_state)
        return self._state_to_dict(loop_state)
    
    def _complete_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Terminal node: Work complete."""
        loop_state = self._dict_to_state(state)
        loop_state.stage = LoopStage.COMPLETE

        self._clear_failure_context(loop_state)

        self._audit("task_complete", {
            "op": "task_complete",
            "task_ref": loop_state.task.id,
            "artifacts": loop_state.artifacts,
        })

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
            progress_cb=self._progress_cb_handler,
            artifacts=artifacts,
            base_ref=base_ref,
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
        """Print a progress line to stdout.

        Normal-path transitions are always printed; per-validator verdicts and
        other fine-grained detail are gated behind ``verbose``.
        """
        if verbose and not self._verbose:
            return
        print(message, flush=True)

    def _progress_cb_handler(self, arg1: Any, arg2: Any = None) -> None:
        """Dual-purpose progress handler for messages (1 arg) and validator verdicts (2 args)."""
        if arg2 is not None:
            self._validator_verdict_cb(arg1, arg2)
        elif isinstance(arg1, str):
            self._progress(arg1)

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

        if severity == "pass":
            self._progress(f"    ✓ {validator_id}: pass{cap_note}", verbose=True)
        elif severity == "warn":
            snippet = f" — {first_line}" if first_line else ""
            self._progress(f"    ⚠️ {validator_id}: warn{cap_note}{snippet}")
        elif severity == "blocker":
            snippet = f" — {first_line}" if first_line else ""
            self._progress(f"    ❌ {validator_id}: blocker{cap_note}{snippet}")
        else:
            snippet = f" — {first_line}" if first_line else ""
            self._progress(f"    💥 {validator_id}: {severity}{cap_note}{snippet}")


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
    from snodo.infrastructure.config import load_llm_config
    llm_cfg = load_llm_config()

    # Initialize coder with LLM config knobs if not passed directly
    if coder is None:
        initial_mode_obj = protocol.get_mode(protocol.initial_mode)
        mode_coder = getattr(initial_mode_obj, "coder", None) if initial_mode_obj else None
        resolved_model = model or DEFAULT_MODEL
        resolved_name = resolve_coder_name(
            model=resolved_model,
            mode_coder=mode_coder,
            cli_coder=coder_name,
            use_mock=use_mock_coder,
        )
        coder = get_coder(
            resolved_name,
            model=resolved_model,
            max_tokens=llm_cfg.coder.max_tokens,
            max_tool_turns=llm_cfg.coder.max_tool_turns,
            workspace_mcp=workspace_mcp,
        )

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
        **custom_functions
    )
    return builder.build_graph()
