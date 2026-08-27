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

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from langgraph.graph import END, StateGraph

import snodo.predicates.scope  # noqa: F401 — registers predicates on import
import snodo.predicates.secrets  # noqa: F401
import snodo.predicates.tests  # noqa: F401
import snodo.validators  # noqa: F401 — registers validators on import

# Import real implementations
from snodo.coders import LiteLLMAdapter, MockAdapter
from snodo.coders.base import SnodoMutationError
from snodo.compiler.models import Protocol, Validator
from snodo.core.interfaces import ExecutionError, Task, ValidatorResult
from snodo.engine.constraints import ConstraintEngine
from snodo.engine.policy import PolicyAction, PolicyEvaluator, policy_decision_to_dict
from snodo.engine.validators import ValidatorRunner
from snodo.infrastructure.config import DEFAULT_MODEL
from snodo.infrastructure.tokens import TokenIssuer, TokenStoreError, ValidationToken
from snodo.tools.git import GitMCP
from snodo.tools.shell import ShellMCP
from snodo.tools.workspace import WorkspaceMCP
from snodo.validators.context import ValidatorContext


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
    halt_type: Optional[str] = None  # "blocked" | "escalated" | "resolution" | "constraint" | "max_iterations" | "wf3" | "validator_error" | "recovery_exhausted"
    pending_disagreement: Optional[Dict[str, Any]] = None
    spawned_subtasks: List[Task] = field(default_factory=list)
    needs_recovery: bool = False
    needs_spec_authoring: bool = False
    spec_authoring_attempts: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    messages: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""


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
    kwargs: dict[str, Any] = {"model": model}
    api_base = ConfigManager.resolve_api_base(model)
    if api_base:
        kwargs["api_base"] = api_base
    return functools.partial(base_fn, **kwargs)


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


def _build_recovery_spec(original_spec: str, failures: list) -> str:
    """Synthesise a recovery spec from the original intent + accumulated failures.

    The original intent is carried forward exactly once, unchanged.  Each
    failure is an entry dict of the form ``{"attempt", "validator_id",
    "severity", "justification"}`` carrying the attempt number that produced
    it, so the spec never wraps a previous recovery spec and the failure list
    accumulates instead of nesting (ADR 021).  The justification is preserved
    verbatim — including the bounded stdout/stderr tail the validator captured.
    """
    lines = [
        "Fix the following failures. Each is a real, observed failure from a "
        "recovery attempt; resolve all of them.",
        "",
        "INTENT (unchanged from the original task):",
        original_spec,
        "",
        "CONSTRAINTS:",
        "- Preserve the original intent and scope; do not add unrelated changes.",
        "- Address every failure listed below.",
    ]

    if failures:
        lines.append("")
        lines.append("FAILURES (accumulated across recovery attempts):")
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

        validator_model = _resolve_model_for_role(config, "validator", DEFAULT_MODEL)
        classifier_model = _resolve_model_for_role(config, "classifier", DEFAULT_MODEL)

        if is_mock_mode_active() or (isinstance(self.coder, MockAdapter) and _base_fn is not None):
            validator_completion_fn = _build_completion_fn(validator_model, _base_fn)
            classifier_completion_fn = _build_completion_fn(classifier_model, _base_fn)
        elif _base_fn is None and not is_mock_mode_active():
            validator_completion_fn = None
            classifier_completion_fn = None
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
    
    def _governance_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Stage 1: Check constraints and resolve pending disagreements."""
        loop_state = self._dict_to_state(state)
        loop_state.stage = LoopStage.GOVERNANCE
        loop_state.iteration += 1

        # Safety net: prevent infinite loops (max 50 iterations)
        if loop_state.iteration > 50:
            loop_state.is_blocked = True
            loop_state.halt_type = "max_iterations"
            loop_state.constraint_violations.append(
                "Max iterations (50) exceeded — possible infinite loop"
            )
            return self._state_to_dict(loop_state)

        # Spec-authoring follow-through: a warn-only pre-execute escalation routed
        # back here. Author an improved spec from the intent + critique, then let
        # it re-validate fresh. Lives in this (live) _governance_node because it
        # shadows the mixin version that holds the same logic.
        if getattr(loop_state, "needs_spec_authoring", False):
            loop_state = self._spec_authoring_reentry(loop_state)
            loop_state.needs_spec_authoring = False
            return self._state_to_dict(loop_state)

        # Load DecisionRecords from session for policy-layer consultation.
        # DecisionRecords are consulted AFTER the blocker HALT in the policy
        # evaluator, so they can NEVER override a genuine blocker (INV3).
        self._decision_records: List[str] = []
        self._authorized_decisions: List[str] = []
        if self._session_manager:
            session = self._session_manager.get_active_session(
                loop_state.current_mode, getattr(self, '_project_root', "")
            )
            if session:
                records = session.checkpoint.decisions.get("decision_records", [])
                if isinstance(records, list):
                    self._decision_records = [r for r in records if isinstance(r, str)]
                auth = session.checkpoint.decisions.get("authorized_decisions", [])
                if isinstance(auth, list):
                    self._authorized_decisions = [a for a in auth if isinstance(a, str)]

        # Respawn coder if a verified set_model(scope=coder) override exists
        self._maybe_respawn_coder()

        # Summarize messages if they've grown too large
        loop_state = self._maybe_summarize(loop_state)

        # On first iteration, classify flow_type and assign/ mint wave
        self._classify_wave(loop_state)

        # On first iteration, run environment preparation
        if loop_state.iteration == 1:
            try:
                from pathlib import Path
                from snodo.infrastructure.environment import prepare_environment, EnvironmentPrepError
                target_path = getattr(self, '_worktree_path', '') or getattr(self, '_project_root', '') or str(Path.cwd())
                prep_res = prepare_environment(
                    target_dir=target_path,
                    protocol=self.protocol,
                    shell_mcp=self.shell_mcp,
                )
                if prep_res.status == "executed":
                    self._progress(f"  Prepared environment: {prep_res.command}")
            except Exception as exc:
                if exc.__class__.__name__ == "EnvironmentPrepError" or isinstance(exc, EnvironmentPrepError):
                    loop_state.is_blocked = True
                    loop_state.halt_type = "validator_error"
                    loop_state.constraint_violations.append(str(exc))
                    cmd_val = getattr(exc, "command", "")
                    code_val = getattr(exc, "exit_code", 1)
                    out_val = getattr(exc, "output", str(exc))
                    self._audit("environment_prep_failed", {
                        "op": "environment_prep_failed",
                        "task_ref": loop_state.task.id,
                        "command": cmd_val,
                        "exit_code": code_val,
                        "output": out_val,
                    })
                    return self._state_to_dict(loop_state)
                raise

        loop_state = self.governance_fn(loop_state, self.protocol)

        self._audit("governance_check", {
            "op": "governance_check",
            "task_ref": loop_state.task.id,
            "mode": loop_state.current_mode,
            "constraints_checked": loop_state.constraints_passed,
        })

        # Track task in messages for agent memory (only on first iteration)
        if loop_state.iteration == 1:
            loop_state.messages.append({
                "role": "user",
                "content": f"Task: {loop_state.task.spec}"
            })

        return self._state_to_dict(loop_state)
    
    def _validate_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Stage 2: Run pre_execute validator quorum."""
        loop_state = self._dict_to_state(state)
        loop_state.stage = LoopStage.VALIDATE
        outcome = "unknown"

        current_mode, validators = self._validator_runner.resolve_validators(
            loop_state.current_mode, "pre_execute"
        )
        if not current_mode:
            loop_state.is_blocked = True
            loop_state.halt_type = "constraint"
            loop_state.constraint_violations.append(f"Invalid mode: {loop_state.current_mode}")
            return self._state_to_dict(loop_state)

        # WF3 runtime guard: explicit empty-validators check
        if not validators:
            loop_state.is_blocked = True
            loop_state.halt_type = "wf3"
            loop_state.constraint_violations.append(
                f"WF3 violation: no pre_execute validators configured "
                f"for mode '{loop_state.current_mode}'"
            )
            self._audit("wf3_runtime_violation", {
                "task_ref": loop_state.task.id,
                "mode": loop_state.current_mode,
                "phase": "pre_execute",
            })
            return self._state_to_dict(loop_state)

        self._progress(
            f"  Validating (pre-execute): {', '.join(v.validator_id for v in validators)}"
        )

        results = self.validator_fn(loop_state.task, validators, self.shell_mcp,
                                    current_mode=loop_state.current_mode,
                                    phase="pre_execute",
                                    authorized_decisions=getattr(self, '_authorized_decisions', []),
                                    decision_issuer=self._decision_issuer,
                                    progress_cb=self._validator_verdict_cb)
        loop_state.validation_results = results

        decision = self.policy_evaluator.evaluate(
            results, self.protocol.disagreement_policy,
            decision_records=getattr(self, '_decision_records', []),
            task_ref=loop_state.task.id,
        )
        loop_state.policy_decision = decision

        outcome = "blocked"
        if decision.action in [PolicyAction.PROCEED, PolicyAction.PROCEED_WITH_LOG]:
            loop_state.validation_token = self._token_issuer.issue_token(
                task_id=loop_state.task.id,
                validator_results=results,
                consensus=self.protocol.disagreement_policy.value,
            )
            outcome = "passed"
        elif decision.action == PolicyAction.HALT:
            loop_state.is_blocked = True
            has_errors = any(getattr(r, 'error', False) for r in results)
            loop_state.halt_type = "validator_error" if has_errors else "blocked"
        elif decision.action == PolicyAction.ESCALATE:
            has_blocker = any(r.severity == "blocker" and not getattr(r, 'error', False) for r in results)
            has_error = any(getattr(r, 'error', False) for r in results)
            # Only spec-quality critique may trigger authoring.  A non-spec
            # objection is about the work, not the wording; laundering it into
            # the spec changes what the task wants (Fixes #35).  If the only
            # escalation is from non-spec validators there is nothing to author
            # from, so escalate normally.
            spec_critique = [
                {"validator_id": r.validator_id, "justification": r.justification}
                for r in results
                if r.severity != "pass" and self._judges_spec(r.validator_id)
            ]
            if (
                not has_blocker and not has_error
                and loop_state.spec_authoring_attempts < 2
                and spec_critique
            ):
                loop_state.needs_spec_authoring = True
                loop_state.metadata["spec_critique"] = spec_critique
                outcome = "escalated"
            else:
                loop_state.is_blocked = True
                loop_state.halt_type = "escalated"
                outcome = "escalated"

            loop_state.pending_disagreement = {
                "phase": "pre_execute",
                "policy": self.protocol.disagreement_policy.value,
                "validator_results": [
                    {"validator_id": r.validator_id, "severity": r.severity, "justification": r.justification}
                    for r in results
                ],
                "policy_decision": {
                    "pass_count": decision.pass_count,
                    "warn_count": decision.warn_count,
                    "blocker_count": decision.blocker_count,
                    "total_count": decision.total_count,
                    "justification": decision.justification,
                },
            }
            self._audit("disagreement_escalated", {
                "op": "disagreement_escalated",
                "phase": "pre_execute",
                "task_ref": loop_state.task.id,
                "policy": self.protocol.disagreement_policy.value,
                "validator_results": loop_state.pending_disagreement["validator_results"],
                "policy_decision": loop_state.pending_disagreement["policy_decision"],
            })

        loop_state.metadata["pre_validation"] = {
            "policy_decision": policy_decision_to_dict(decision),
            "validator_results": [r.model_dump() for r in results],
            "outcome": outcome,
        }

        if loop_state.is_blocked:
            self._auto_write_pending_decisions(loop_state, results)
            self._auto_write_failure_context(loop_state, results)

        self._audit("validate", {
            "op": "validate",
            "phase": "pre_execute",
            "task_ref": loop_state.task.id,
            "validators_invoked": [v.validator_id for v in validators],
            "results": _build_audit_results(validators, results, getattr(getattr(self, "_validator_runner", None), "last_cap_originals", None)),
            "outcome": outcome,
            "policy_decision": str(decision.action.value) if decision else None,
        })

        return self._state_to_dict(loop_state)

    def _execute_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Stage 3: Execute task (REAL IMPLEMENTATION - writes files!)."""
        loop_state = self._dict_to_state(state)
        loop_state.stage = LoopStage.EXECUTE

        # Collect project context once (cached on builder)
        if self._project_context_cache is None:
            self._project_context_cache = self._collect_project_context(self.workspace_mcp)
        loop_state.metadata["project_context"] = self._project_context_cache

        if self._token_issuer.verify_token(
            loop_state.validation_token,
            expected_task_id=loop_state.task.id,
        ):
            # Token verified — safe to use (never None here)
            assert loop_state.validation_token is not None
            # Single-use: consume at the dispatch boundary (the point where the
            # token authorises irreversible work). The INSERT is the claim —
            # atomic across processes. Fail closed if the store is down.
            try:
                self._token_issuer.consume_token(loop_state.validation_token)
            except TokenStoreError as e:
                loop_state.is_blocked = True
                loop_state.halt_type = "internal_error"
                loop_state.constraint_violations.append(
                    f"Token store unavailable: {e}"
                )
                loop_state.metadata["post_validation"] = {
                    "outcome": "skipped",
                    "reason": f"Token store unavailable: {e}",
                }
                self._audit("token_store_unavailable", {
                    "op": "token_store_unavailable",
                    "task_ref": loop_state.task.id,
                    "error": str(e),
                })
                return self._state_to_dict(loop_state)
            try:
                self._progress("  Coder dispatched")
                artifacts = self.executor_fn(
                    loop_state.task,
                    loop_state.validation_token,
                    self.coder,
                    self.workspace_mcp,
                    self.git_mcp,
                    memory_summary=loop_state.summary,
                    project_context=self._project_context_cache,
                )
                self._progress(f"  Coder returned ({len(artifacts)} artifact(s))")
            except SnodoMutationError as e:
                # An in-place-writing coder mutated protected .snodo/ state.
                # This is a governance violation (INV3-class), not an
                # execution fault: block with a terminal halt, record the
                # attempt in the audit trail, and leave the tree for operator
                # inspection (Fixes #52, ADR 027).
                loop_state.is_blocked = True
                loop_state.halt_type = "blocked"
                loop_state.constraint_violations.append(str(e))
                loop_state.metadata["post_validation"] = {
                    "outcome": "skipped",
                    "reason": str(e),
                }
                self._audit("snodo_mutation_blocked", {
                    "op": "snodo_mutation_blocked",
                    "task_ref": loop_state.task.id,
                    "mode": loop_state.current_mode,
                    "paths": list(getattr(e, "paths", [])),
                    "error": str(e),
                })
                return self._state_to_dict(loop_state)
            except ExecutionError as e:
                loop_state.is_blocked = True
                loop_state.halt_type = "internal_error"
                loop_state.constraint_violations.append(str(e))
                # Mark post-validation as skipped, not passed: the execute step
                # failed, so there is nothing to validate and a green
                # post-validation on zero artifacts must never be emitted.
                loop_state.metadata["post_validation"] = {
                    "outcome": "skipped",
                    "reason": str(e),
                }
                self._audit("execution_failed", {
                    "op": "execution_failed",
                    "task_ref": loop_state.task.id,
                    "error": str(e),
                })
                return self._state_to_dict(loop_state)

            loop_state.artifacts.extend(artifacts)

            # Housekeeping: clear the in-memory slot (enforcement is the store).
            loop_state.validation_token = None
            self._audit("token_consumed", {
                "op": "token_consumed",
                "task_ref": loop_state.task.id,
            })

        self._audit("dispatch", {
            "op": "dispatch",
            "task_ref": loop_state.task.id,
            "token_id": loop_state.task.id,
            "mode": loop_state.current_mode,
            "artifacts_count": len(loop_state.artifacts),
        })

        # Track execution in messages for agent memory
        artifact_summary = ", ".join(loop_state.artifacts) if loop_state.artifacts else "none"
        loop_state.messages.append({
            "role": "assistant",
            "content": f"Executed task '{loop_state.task.spec}' in mode "
                       f"'{loop_state.current_mode}'. Artifacts: {artifact_summary}."
        })

        return self._state_to_dict(loop_state)

    def _post_validate_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Stage 3b: Run post_execute validators (quality gate)."""
        loop_state = self._dict_to_state(state)
        loop_state.stage = LoopStage.VALIDATE  # Reuse VALIDATE stage enum

        # Re-evaluate constraints with post-execute context
        # (artifacts populated, git diff available)
        self._constraint_engine.evaluate(loop_state, "post_validate", self._audit)

        current_mode, post_validators = self._validator_runner.resolve_validators(
            loop_state.current_mode, "post_execute"
        )
        if not current_mode or not post_validators:
            self._audit("post_validate_bypassed", {
                "task_ref": loop_state.task.id if loop_state.task else None,
                "mode": loop_state.current_mode,
                "reason": "no_post_execute_validators",
            })
            return self._state_to_dict(loop_state)

        # Run post_execute validators
        self._progress(
            f"  Post-validating: {', '.join(v.validator_id for v in post_validators)}"
        )
        results = self.validator_fn(loop_state.task, post_validators, self.shell_mcp,
                                    current_mode=loop_state.current_mode,
                                    phase="post_execute",
                                    authorized_decisions=getattr(self, '_authorized_decisions', []),
                                    decision_issuer=self._decision_issuer,
                                    progress_cb=self._validator_verdict_cb,
                                    artifacts=list(loop_state.artifacts))

        # Merge post-validate results with existing results
        loop_state.validation_results = loop_state.validation_results + results

        # Evaluate policy on post-execute results
        decision = self.policy_evaluator.evaluate(
            results,
            self.protocol.disagreement_policy,
            decision_records=getattr(self, '_decision_records', []),
            task_ref=loop_state.task.id,
        )

        post_outcome = "passed"
        if decision.action == PolicyAction.HALT:
            has_errors = any(getattr(r, 'error', False) for r in results)
            if has_errors:
                # Validator error — TERMINAL (INV3)
                loop_state.is_blocked = True
                loop_state.halt_type = "validator_error"
                loop_state.constraint_violations.append(
                    "Validator error: " + decision.justification
                )
                post_outcome = "blocked"
            elif self._is_recoverable(loop_state, results):
                # Overridable blocker / warning — RECOVERABLE
                self._spawn_recovery_subtask(loop_state, results, decision)
                post_outcome = "recovery"
            else:
                # Non-overridable blocker — TERMINAL
                loop_state.is_blocked = True
                loop_state.halt_type = "blocked"
                loop_state.constraint_violations.append(
                    "Post-execute validation failed: " + decision.justification
                )
                post_outcome = "blocked"
        elif decision.action == PolicyAction.ESCALATE:
            # ESCALATE is always RECOVERABLE — spawn fix subtask
            self._spawn_recovery_subtask(loop_state, results, decision)
            post_outcome = "recovery"

        loop_state.policy_decision = decision
        loop_state.metadata["post_validation"] = {
            "policy_decision": policy_decision_to_dict(decision),
            "validator_results": [r.model_dump() for r in results],
            "outcome": post_outcome,
        }

        if loop_state.is_blocked:
            self._auto_write_pending_decisions(loop_state, results)
            self._auto_write_failure_context(loop_state, results)

        self._audit("validate", {
            "op": "validate",
            "phase": "post_execute",
            "task_ref": loop_state.task.id,
            "validators_invoked": [v.validator_id for v in post_validators],
            "results": _build_audit_results(post_validators, results, getattr(getattr(self, "_validator_runner", None), "last_cap_originals", None)),
            "outcome": post_outcome,
        })

        return self._state_to_dict(loop_state)

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
            self._audit("recovery_stalled", {
                "op": "recovery_stalled",
                "task_ref": loop_state.task.id,
                "depth": current_depth,
                "validator_ids": [f["validator_id"] for f in new_failures],
            })
            return

        # Accumulate failures across attempts rather than replacing them.
        accumulated = list(loop_state.task.prior_failures or []) + new_failures
        spec = _build_recovery_spec(root_spec, accumulated)

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
            depth=current_depth + 1,
        )
        loop_state.spawned_subtasks.append(fix_task)
        loop_state.needs_recovery = True

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

    def _route_after_post_validation(self, state: Dict[str, Any]) -> str:
        """Route after post-validation: recovery, proceed, or block."""
        loop_state = self._dict_to_state(state)
        if loop_state.needs_recovery:
            decision = "recovery"
        elif loop_state.is_blocked:
            decision = "blocked"
        else:
            decision = "move_next"
        self._audit("post_validation_route", {
            "op": "post_validation_route",
            "task_ref": loop_state.task.id,
            "decision": decision,
        })
        return decision

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

    def _route_after_validation(self, state: Dict[str, Any]) -> str:
        """Route after validation based on policy decision."""
        loop_state = self._dict_to_state(state)
        
        if loop_state.is_blocked:
            return "blocked"
        elif loop_state.validation_token and self._token_issuer.verify_token(
            loop_state.validation_token,
            expected_task_id=loop_state.task.id,
        ):
            return "execute"
        else:
            return "governance"

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
        """Print a per-validator verdict as it lands (verbose only)."""
        severity = getattr(result, "severity", "?")
        self._progress(f"    ✓ {validator_id}: {severity}", verbose=True)


def _build_audit_results(
    validators: list, results: list, cap_originals: Optional[dict] = None
) -> list:
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
                entry["severity_at_cap"] = True
        if cap_originals and r.validator_id in cap_originals:
            entry["severity_original"] = cap_originals[r.validator_id]
        audit_results.append(entry)
    return audit_results


def _slugify(spec: str, max_words: int = 5) -> str:
    """Convert a task spec into a branch-safe slug.

    Takes the first *max_words* words, lowercases, hyphenates,
    and strips non-alphanumeric characters.
    """
    import re
    words = spec.strip().split()[:max_words]
    slug = "-".join(words).lower()
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    return slug


def _task_branch_name(task_id: str, spec: str) -> str:
    """Build a branch name: task/{task_id}/{slug}."""
    return f"task/{task_id}/{_slugify(spec)}"


def _branch_exists(git_mcp: Any, name: str) -> bool:
    """Return True if *name* is an existing branch head."""
    try:
        return name in git_mcp.repo.heads
    except Exception:
        return False


def build_protocol_graph(
    protocol: Protocol,
    project_root: Optional[str] = None,
    use_mock_coder: bool = False,
    model: Optional[str] = None,
    coder: Optional[Any] = None,
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

    from snodo.coders import resolve_adapter_class
    from snodo.infrastructure.config import load_llm_config
    llm_cfg = load_llm_config()

    # Initialize coder with LLM config knobs if not passed directly
    if coder is None:
        resolved_model = model or DEFAULT_MODEL
        adapter_cls = resolve_adapter_class(resolved_model)
        if use_mock_coder:
            from snodo.coders.mock import set_mock_mode
            set_mock_mode(True)
            coder = MockAdapter()
        else:
            coder = adapter_cls(
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