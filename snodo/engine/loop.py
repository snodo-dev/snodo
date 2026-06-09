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

from typing import Dict, Any, List, Optional, Callable, Union
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from langgraph.graph import StateGraph, END

from snodo.compiler.models import Protocol, Validator
from snodo.core.interfaces import Task, ValidatorResult, TaskSpec, ExecutionError
from snodo.infrastructure.tokens import ValidationToken, TokenIssuer
from snodo.infrastructure.config import DEFAULT_MODEL
from snodo.engine.policy import PolicyEvaluator, PolicyAction, policy_decision_to_dict
from snodo.engine.constraints import ConstraintEngine
from snodo.engine.validators import ValidatorRunner

# Import real implementations
from snodo.coders import LiteLLMAdapter, MockAdapter
from snodo.mcp.workspace import WorkspaceMCP
from snodo.mcp.git import GitMCP
from snodo.mcp.shell import ShellMCP
import snodo.predicates.scope  # noqa: F401 — registers predicates on import
import snodo.predicates.tests  # noqa: F401
import snodo.predicates.secrets  # noqa: F401
import snodo.validators  # noqa: F401 — registers validators on import
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
    halt_type: Optional[str] = None  # "blocked" | "escalated" | "resolution" | "constraint" | "max_iterations" | "wf3" | "validator_error"
    pending_disagreement: Optional[Dict[str, Any]] = None
    spawned_subtasks: List[Task] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    messages: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""


class GraphBuilder:
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
        self.protocol = protocol
        self.workspace_mcp = workspace_mcp
        self.git_mcp = git_mcp
        self.shell_mcp = shell_mcp
        self.coder = coder or MockAdapter()
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
        self._completion_fn = getattr(self.coder, "_completion_fn", None) or \
                              getattr(self.coder, "completion_fn", None)
        self._default_model = getattr(self.coder, "model", DEFAULT_MODEL)

        # Validators need their own completion_fn — the coder may not
        # use LiteLLM (e.g. OpenCodeAdapter uses HTTP).  Fall back to a
        # partial litellm.completion bound to the configured default model.
        # We use ConfigManager directly — self._default_model may be a
        # non-LiteLLM string like "opencode/google/gemini-3.5-flash".
        # Always bind the validator model explicitly — the coder's
        # _completion_fn (if present) provides the authenticated call
        # mechanism but has no model bound.
        from litellm import completion as litellm_completion
        import functools
        from snodo.cli.config import ConfigManager, _set_api_key_env

        config = ConfigManager().load()
        validator_model = (
            config.get("llm", {}).get("validator_llm", {}).get("model")
            or config.get("model")
            or DEFAULT_MODEL
        )
        _set_api_key_env(ConfigManager(), validator_model)

        base_fn = getattr(self.coder, "_completion_fn", None) or litellm_completion
        validator_completion_fn = functools.partial(
            base_fn, model=validator_model,
        )
        validator_default_model = validator_model

        self._validator_runner = ValidatorRunner(
            protocol=self.protocol,
            completion_fn=validator_completion_fn,
            default_model=validator_default_model,
            validator_config=validator_config,
            audit_log=self._audit_log,
            workspace_mcp=workspace_mcp,
            git_mcp=git_mcp,
            session_manager=session_manager,
        )

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
        workflow.add_edge("execute", "post_validate")
        workflow.add_conditional_edges(
            "post_validate",
            self._route_after_post_validation,
            {
                "move_next": "move_next",
                "blocked": "blocked"
            }
        )
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

        results = self.validator_fn(loop_state.task, validators, self.shell_mcp,
                                    current_mode=loop_state.current_mode,
                                    phase="pre_execute",
                                    authorized_decisions=getattr(self, '_authorized_decisions', []),
                                    decision_issuer=self._decision_issuer)
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
            has_errors = any(r.severity == "error" for r in results)
            loop_state.halt_type = "validator_error" if has_errors else "blocked"
        elif decision.action == PolicyAction.ESCALATE:
            loop_state.is_blocked = True
            loop_state.halt_type = "escalated"
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
            outcome = "escalated"
            self._audit("disagreement_escalated", {
                "op": "disagreement_escalated",
                "phase": "pre_execute",
                "task_ref": loop_state.task.id,
                "policy": self.protocol.disagreement_policy.value,
                "validator_results": loop_state.pending_disagreement["validator_results"],
                "policy_decision": loop_state.pending_disagreement["policy_decision"],
            })

        if loop_state.is_blocked:
            self._auto_write_pending_decisions(loop_state, results)
            self._auto_write_failure_context(loop_state, results)

        self._audit("validate", {
            "op": "validate",
            "phase": "pre_execute",
            "task_ref": loop_state.task.id,
            "validators_invoked": [v.validator_id for v in validators],
            "results": _build_audit_results(validators, results),
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
            try:
                artifacts = self.executor_fn(
                    loop_state.task,
                    loop_state.validation_token,
                    self.coder,
                    self.workspace_mcp,
                    self.git_mcp,
                    memory_summary=loop_state.summary,
                    project_context=self._project_context_cache,
                )
            except ExecutionError as e:
                loop_state.is_blocked = True
                loop_state.halt_type = "execution_error"
                loop_state.constraint_violations.append(str(e))
                self._audit("execution_failed", {
                    "op": "execution_failed",
                    "task_ref": loop_state.task.id,
                    "error": str(e),
                })
                return self._state_to_dict(loop_state)

            loop_state.artifacts.extend(artifacts)

            # Single-use: consume the token after successful dispatch
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
        results = self.validator_fn(loop_state.task, post_validators, self.shell_mcp,
                                    current_mode=loop_state.current_mode,
                                    phase="post_execute",
                                    authorized_decisions=getattr(self, '_authorized_decisions', []),
                                    decision_issuer=self._decision_issuer)

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
            loop_state.is_blocked = True
            has_errors = any(r.severity == "error" for r in results)
            loop_state.halt_type = "validator_error" if has_errors else "blocked"
            loop_state.constraint_violations.append(
                "Post-execute validation failed: " + decision.justification
            )
            post_outcome = "blocked"
        elif decision.action == PolicyAction.ESCALATE:
            loop_state.is_blocked = True
            loop_state.halt_type = "escalated"
            loop_state.pending_disagreement = {
                "phase": "post_execute",
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
            post_outcome = "escalated"
            self._audit("disagreement_escalated", {
                "op": "disagreement_escalated",
                "phase": "post_execute",
                "task_ref": loop_state.task.id,
                "policy": self.protocol.disagreement_policy.value,
                "validator_results": loop_state.pending_disagreement["validator_results"],
                "policy_decision": loop_state.pending_disagreement["policy_decision"],
            })

        if loop_state.is_blocked:
            self._auto_write_pending_decisions(loop_state, results)
            self._auto_write_failure_context(loop_state, results)

        self._audit("validate", {
            "op": "validate",
            "phase": "post_execute",
            "task_ref": loop_state.task.id,
            "validators_invoked": [v.validator_id for v in post_validators],
            "results": _build_audit_results(post_validators, results),
            "outcome": post_outcome,
        })

        return self._state_to_dict(loop_state)

    def _route_after_post_validation(self, state: Dict[str, Any]) -> str:
        """Route after post-validation: proceed or block."""
        loop_state = self._dict_to_state(state)
        decision = "blocked" if loop_state.is_blocked else "move_next"
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
    ) -> List[ValidatorResult]:
        """Validator dispatch via registry (Task 7.20).

        Builds a single ValidatorContext for the pass, then looks up
        each validator_spec.validator_type in the registry.  Falls back
        to the LLMValidator catch-all for registered types + criteria,
        or stub results for unrecognised / no-LLM cases.

        Kept as full implementation (not delegation) so tests can
        monkey-patch ``self._dispatch_one`` and have it take effect.
        """
        from snodo.validators.registry import _default_registry as reg

        mode_obj = self.protocol.get_mode(current_mode)
        _vcfg = self._validator_runner._validator_config
        if _vcfg is None:
            from snodo.infrastructure.config import load_llm_config, ConfigLoadError
            try:
                _vcfg = load_llm_config().validator
            except ConfigLoadError as e:
                return [
                    ValidatorResult(
                        validator_id="config",
                        severity="blocker",
                        justification=f"Config error: {e}",
                    )
                ]
        context = ValidatorContext(
            task=task,
            current_mode=mode_obj,
            protocol=self.protocol,
            artifacts=[],
            audit_log=self._audit_log,
            mode_name=mode_obj.name if mode_obj else "",
            mode_tools=list(mode_obj.tools) if mode_obj else [],
            mode_transitions=dict(mode_obj.transitions) if mode_obj else {},
            mode_validator_refs=list(mode_obj.validators) if mode_obj else [],
            completion_fn=self._get_completion_fn(),
            model=getattr(self.coder, "model", DEFAULT_MODEL),
            working_directory=str(Path.cwd()) if not self.workspace_mcp
            else str(getattr(self.workspace_mcp, "project_root", Path.cwd())),
            workspace_mcp=self.workspace_mcp,
            git_mcp=self.git_mcp,
            phase=phase,
            max_tokens=_vcfg.max_tokens,
            max_tool_turns=_vcfg.max_tool_turns,
        )

        results = []
        for v in validators:
            result = self._dispatch_one(v, context, reg)
            if v.severity_cap is not None:
                from snodo.compiler.models import Severity
                if Severity(result.severity) > v.severity_cap:
                    result = ValidatorResult(
                        validator_id=result.validator_id,
                        severity=v.severity_cap.value,
                        justification=result.justification,
                    )
            results.append(result)
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

    def _auto_write_pending_decisions(self, loop_state: Any, results: list) -> None:
        """Write pending_decision entries for every blocking/escalating validator.

        Called on HALT and ESCALATE decisions so ``snodo authorize`` can find
        the proposals without the orchestrator calling propose_adjudicate.
        """
        if not self._session_manager or not self._session_id:
            return

        task_id = loop_state.task.id
        try:
            session = self._session_manager.load_session(self._session_id)
        except Exception:
            return

        pending = session.checkpoint.decisions.get("pending_decisions", {})
        if not isinstance(pending, dict):
            pending = {}

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        for r in results:
            if r.severity not in ("blocker", "warn", "error"):
                continue
            entry = {
                "type": "adjudicate",
                "validator_id": r.validator_id,
                "decision": "proceed",
                "justification": r.justification,
                "severity": r.severity,
                "proposed_by": "engine",
                "timestamp": now,
            }
            pending[task_id] = entry

        self._session_manager.update_decision(
            self._session_id, "pending_decisions", pending,
        )

    def _auto_write_failure_context(self, loop_state: Any, results: list) -> None:
        """Persist structured failure context for retry when a task halts.

        Written to ``session.checkpoint.decisions["task_failure"][task_id]``.
        Separate from pending_decisions — this is operational state, not
        a governance record.
        """
        if not self._session_manager or not self._session_id:
            return

        task_id = loop_state.task.id
        try:
            session = self._session_manager.load_session(self._session_id)
        except Exception:
            return

        failures = session.checkpoint.decisions.get("task_failure", {})
        if not isinstance(failures, dict):
            failures = {}

        existing = failures.get(task_id, {}) if isinstance(failures.get(task_id), dict) else {}
        attempt = existing.get("attempt", 0) + 1

        from datetime import datetime, timezone
        branch_name = _task_branch_name(task_id, loop_state.task.spec)

        failures[task_id] = {
            "spec": loop_state.task.spec,
            "branch": branch_name,
            "attempt": attempt,
            "failed_validators": [
                {
                    "validator_id": r.validator_id,
                    "severity": r.severity,
                    "justification": r.justification,
                }
                for r in results
                if r.severity in ("blocker", "warn", "error")
            ],
            "files_changed": list(loop_state.artifacts),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self._session_manager.update_decision(
            self._session_id, "task_failure", failures,
        )

    def _clear_failure_context(self, loop_state: Any) -> None:
        """Remove failure context for a task when execution succeeds."""
        if not self._session_manager or not self._session_id:
            return

        task_id = loop_state.task.id
        try:
            session = self._session_manager.load_session(self._session_id)
        except Exception:
            return

        failures = session.checkpoint.decisions.get("task_failure", {})
        if isinstance(failures, dict) and task_id in failures:
            del failures[task_id]
            try:
                self._session_manager.update_decision(
                    self._session_id, "task_failure", failures,
                )
            except Exception:
                pass

    def _maybe_respawn_coder(self) -> None:
        """Respawn the coder if a verified set_model(scope=coder) override exists.

        Reads self._authorized_decisions (loaded by _governance_node),
        verifies each via the verify-only issuer, and rebuilds the coder
        when the override model differs from the current coder's model.

        Also updates the two values captured at __init__ so the validator
        default_model stays in sync:
          self._completion_fn  → new coder's completion fn
          self._default_model  → new model
          self._validator_runner._completion_fn
          self._validator_runner._default_model

        Idempotent: does nothing when no override exists or when the
        override model equals the current model.
        """
        if not self._authorized_decisions or not self._decision_issuer:
            return

        verified = self._decision_issuer.find_set_model_overrides(
            self._authorized_decisions,
        )
        override = next(
            (p for p in verified if p.get("scope") == "coder"), None
        )
        if override is None:
            return

        new_model = override.get("proposed_model", "")
        if not new_model or new_model == getattr(self.coder, "model", ""):
            return  # already on this model — idempotent

        # Build a fresh coder with the new model
        from snodo.coders import resolve_adapter_class
        from snodo.infrastructure.config import load_llm_config

        llm_cfg = load_llm_config()
        adapter_cls = resolve_adapter_class(new_model)
        fresh_coder = adapter_cls(
            model=new_model,
            max_tokens=llm_cfg.coder.max_tokens,
            max_tool_turns=llm_cfg.coder.max_tool_turns,
            workspace_mcp=self.workspace_mcp,
        )

        old_model = getattr(self.coder, "model", "")
        self.coder = fresh_coder
        self._completion_fn = getattr(fresh_coder, "_completion_fn", None) or \
                              getattr(fresh_coder, "completion_fn", None)
        self._default_model = new_model

        # Keep the validator runner in sync — it holds its own copies
        self._validator_runner._completion_fn = self._completion_fn
        self._validator_runner._default_model = self._default_model

        self._audit("coder_respawned", {
            "op": "coder_respawned",
            "old_model": old_model,
            "new_model": new_model,
        })

    @staticmethod
    def _init_summary_model():
        """Try to create a cheap summary model. Returns None if unavailable."""
        try:
            from snodo.infrastructure.memory import create_summary_model
            return create_summary_model()
        except Exception:
            return None

    def _maybe_summarize(self, loop_state: LoopState) -> LoopState:
        """Summarize messages if they exceed token threshold.

        Uses LLM summary when model is available, otherwise truncates
        messages and builds a simple text summary from discarded messages.

        Threshold: ~8000 tokens (approx 4 chars per token).
        """
        total_chars = sum(len(m.get("content", "")) for m in loop_state.messages)
        token_estimate = total_chars // 4

        if token_estimate < 8000:
            return loop_state

        if self._summary_model is not None:
            try:
                summary_prompt = (
                    "Summarize the following conversation history concisely "
                    "(max 512 tokens). Focus on key decisions, artifacts "
                    "produced, and important context:\n\n"
                )
                for msg in loop_state.messages:
                    summary_prompt += f"{msg['role']}: {msg['content']}\n"

                response = self._summary_model.invoke(summary_prompt)
                loop_state.summary = response.content
                loop_state.messages = loop_state.messages[-3:]
                return loop_state
            except Exception:
                pass  # Fall through to truncation

        # Fallback: truncate messages, keep most recent 3
        discarded = loop_state.messages[:-3]
        if discarded:
            snippets = [m.get("content", "")[:100] for m in discarded]
            loop_state.summary = "Previous: " + "; ".join(snippets)
        loop_state.messages = loop_state.messages[-3:]
        return loop_state

    def _collect_project_context(
        self, workspace_mcp: Optional[WorkspaceMCP]
    ) -> Dict[str, Any]:
        """Collect project context: language, structure, key configs.

        Args:
            workspace_mcp: Workspace MCP for file operations

        Returns:
            Dict with language, structure, and config_files keys
        """
        context: Dict[str, Any] = {
            "language": "unknown",
            "structure": "",
            "config_files": {},
        }
        if not workspace_mcp:
            return context

        # Language detection from marker files
        lang_markers = [
            ("package.json", "javascript"),
            ("tsconfig.json", "typescript"),
            ("pyproject.toml", "python"),
            ("setup.py", "python"),
            ("setup.cfg", "python"),
            ("Cargo.toml", "rust"),
            ("go.mod", "go"),
            ("pom.xml", "java"),
            ("build.gradle", "java"),
        ]
        for marker, lang in lang_markers:
            if workspace_mcp.file_exists(marker):
                context["language"] = lang
                break

        # Directory tree via BFS (depth 3)
        context["structure"] = self._build_dir_tree(workspace_mcp, max_depth=3)

        # Key config files
        config_candidates = [
            "package.json", "tsconfig.json", "pyproject.toml",
            "setup.py", "setup.cfg", "Cargo.toml", "go.mod",
        ]
        for cfg in config_candidates:
            try:
                content = workspace_mcp.read_file(cfg)
                # Truncate large configs to first 2000 chars
                context["config_files"][cfg] = content[:2000]
            except FileNotFoundError:
                pass

        return context

    @staticmethod
    def _build_dir_tree(
        workspace_mcp: WorkspaceMCP, max_depth: int = 3
    ) -> str:
        """Build directory tree via iterative BFS.

        Args:
            workspace_mcp: Workspace MCP for listing files
            max_depth: Maximum traversal depth

        Returns:
            Formatted tree string
        """
        lines: List[str] = []
        # Queue entries: (relative_path, depth)
        queue: List[tuple] = [(".", 0)]

        while queue:
            current_path, depth = queue.pop(0)
            try:
                entries = sorted(workspace_mcp.list_files(current_path))
            except (FileNotFoundError, ValueError):
                continue

            for entry in entries:
                # Skip hidden directories and common noise
                if entry.startswith(".") or entry in ("node_modules", "__pycache__", ".git"):
                    continue
                indent = "  " * depth
                child_path = entry if current_path == "." else f"{current_path}/{entry}"
                # Check if it's a directory by trying to list it
                try:
                    workspace_mcp.list_files(child_path)
                    lines.append(f"{indent}{entry}/")
                    if depth < max_depth - 1:
                        queue.append((child_path, depth + 1))
                except (FileNotFoundError, ValueError):
                    lines.append(f"{indent}{entry}")

        return "\n".join(lines[:200])  # Cap at 200 lines

    def _default_executor(
        self,
        task: Task,
        token: ValidationToken,  # JWT-backed, from tokens.py (7.7)
        coder: Union[LiteLLMAdapter, MockAdapter],
        workspace_mcp: Optional[WorkspaceMCP],
        git_mcp: Optional[GitMCP],
        memory_summary: str = "",
        project_context: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Default executor - REAL IMPLEMENTATION.

        This actually:
        1. Calls coder to generate code (returns CodeArtifact with FileArtifact list)
        2. Iterates file operations: write or delete via workspace MCP
        3. Stages and commits via git MCP
        """
        artifacts = []

        # Inject workspace into coder for read-before-write tool loop
        if workspace_mcp and hasattr(coder, "workspace_mcp") and coder.workspace_mcp is None:
            coder.workspace_mcp = workspace_mcp

        # Generate code using coder with context
        spec = TaskSpec(
            description=task.spec,
            constraints=[],
            memory_summary=memory_summary,
            project_context=project_context or {},
        )

        # Branch isolation: create/checkout task branch before coder runs
        if git_mcp:
            branch_name = _task_branch_name(task.id, task.spec)
            if _branch_exists(git_mcp, branch_name):
                git_mcp.checkout_branch(branch_name)
            else:
                git_mcp.create_branch(branch_name)

        try:
            code_artifact = coder.implement(spec)

            # If workspace available, process file operations
            if workspace_mcp:
                artifact_paths = []
                for file_op in code_artifact.files:
                    if file_op.action == "delete":
                        if not getattr(coder, "skip_workspace_write", False):
                            workspace_mcp.delete_file(file_op.path)
                    else:
                        if not getattr(coder, "skip_workspace_write", False):
                            workspace_mcp.write_file(file_op.path, file_op.content)
                    artifact_paths.append(file_op.path)
                    artifacts.append(file_op.path)

                if not artifact_paths and not getattr(coder, "skip_engine_commit", False):
                    raise ExecutionError("Coder produced no file operations")

                # If git available, stage and commit
                if git_mcp and artifact_paths and not getattr(coder, "skip_engine_commit", False):
                    try:
                        git_mcp.stage_files(artifact_paths)
                        git_mcp.commit(f"feat: {task.spec}")
                        artifacts.append("git_commit")
                    except Exception as e:
                        # Git operation failed, not critical
                        artifacts.append(f"git_error: {str(e)}")
            else:
                # No workspace, just return stub
                artifacts.append(f"code_generated_for_{task.id}")

        except ExecutionError:
            raise
        except Exception as e:
            # Code generation failed
            artifacts.append(f"error: {str(e)}")

        if any(a.startswith("error:") for a in artifacts):
            raise ExecutionError(f"Coder execution failed: {artifacts}")

        return artifacts
    
    def _dict_to_state(self, d: Dict[str, Any]) -> LoopState:
        """Convert dict to LoopState."""
        task_dict = d.get("task", {})
        task = Task(
            id=task_dict.get("id", ""),
            spec=task_dict.get("spec", ""),
            parent_task_ref=task_dict.get("parent_task_ref"),
            depth=task_dict.get("depth", 0),
        )
        
        results = []
        for r in d.get("validation_results", []):
            results.append(ValidatorResult(
                validator_id=r.get("validator_id", ""),
                severity=r.get("severity", "pass"),
                justification=r.get("justification", "")
            ))
        
        token = None
        if d.get("validation_token"):
            token_dict = d["validation_token"]
            jwt_str = token_dict.get("jwt", "")
            if jwt_str:
                # Reconstruct token from JWT string via the issuer
                token = ValidationToken(jwt=jwt_str)
                payload = self._token_issuer.decode_token(token)
                if payload:
                    token = ValidationToken(
                        jwt=jwt_str,
                        task_id=payload.get("task_id", ""),
                        validator_signatures=payload.get("validator_signatures", []),
                        consensus=payload.get("consensus", ""),
                        issued_at=payload.get("iat", ""),
                        expires_at=payload.get("exp", ""),
                    )
        
        return LoopState(
            task=task,
            current_mode=d.get("current_mode", ""),
            validation_results=results,
            validation_token=token,
            artifacts=d.get("artifacts", []),
            stage=LoopStage(d.get("stage", "governance")),
            iteration=d.get("iteration", 0),
            constraints_passed=d.get("constraints_passed", True),
            constraint_violations=d.get("constraint_violations", []),
            policy_decision=d.get("policy_decision"),
            is_complete=d.get("is_complete", False),
            is_blocked=d.get("is_blocked", False),
            halt_type=d.get("halt_type"),
            pending_disagreement=d.get("pending_disagreement"),
            spawned_subtasks=[],
            metadata=d.get("metadata", {}),
            messages=d.get("messages", []),
            summary=d.get("summary", ""),
        )
    
    def _state_to_dict(self, state: LoopState) -> Dict[str, Any]:
        """Convert LoopState to dict."""
        return {
            "task": {
                "id": state.task.id,
                "spec": state.task.spec,
                "parent_task_ref": state.task.parent_task_ref,
                "depth": state.task.depth,
            },
            "current_mode": state.current_mode,
            "validation_results": [
                {
                    "validator_id": r.validator_id,
                    "severity": r.severity,
                    "justification": r.justification
                }
                for r in state.validation_results
            ],
            "validation_token": {
                "jwt": state.validation_token.jwt,
            } if state.validation_token else None,
            "artifacts": state.artifacts,
            "stage": state.stage.value,
            "iteration": state.iteration,
            "constraints_passed": state.constraints_passed,
            "constraint_violations": state.constraint_violations,
            "policy_decision": policy_decision_to_dict(state.policy_decision),
            "is_complete": state.is_complete,
            "is_blocked": state.is_blocked,
            "halt_type": state.halt_type,
            "pending_disagreement": state.pending_disagreement,
            "metadata": state.metadata,
            "messages": state.messages,
            "summary": state.summary,
        }


def _build_audit_results(validators: list, results: list) -> list:
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
                # We don't have the original here, but we can flag that
                # the result sits at the cap boundary
                entry["severity_at_cap"] = True
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
    checkpointer=None,
    audit_log: Any = None,
    session_manager: Any = None,
    session_id: Optional[str] = None,
    **custom_functions
) -> StateGraph:
    """Convenience function to build graph with MCP integration.

    Args:
        protocol: Protocol specification
        project_root: Project root for MCP services (defaults to current directory)
        use_mock_coder: If True, use MockCoderAdapter instead of real LLM
        model: Model identifier for the coder (default: claude-sonnet-4-20250514)
        checkpointer: LangGraph checkpointer for persistent agent memory
        audit_log: Optional AuditLog for INV4 event logging
        session_manager: Optional SessionManager for INV5 session state
        session_id: Optional active session ID to tag on every audit event
        **custom_functions: Optional overrides

    Returns:
        Executable StateGraph with real MCP integration
    """
    if project_root is None:
        from snodo.infrastructure.paths import resolve_project_root
        project_root = str(resolve_project_root() or Path.cwd())

    # Initialize MCP services
    workspace_mcp = WorkspaceMCP(project_root)
    git_mcp = GitMCP(project_root)
    shell_mcp = ShellMCP(project_root)

    # Initialize coder with LLM config knobs
    from snodo.infrastructure.config import load_llm_config
    from snodo.coders import resolve_adapter_class
    llm_cfg = load_llm_config()
    resolved_model = model or DEFAULT_MODEL
    adapter_cls = resolve_adapter_class(resolved_model)
    coder: Union[LiteLLMAdapter, MockAdapter]
    if use_mock_coder:
        coder = MockAdapter()
    else:
        coder = adapter_cls(
            model=resolved_model,
            max_tokens=llm_cfg.coder.max_tokens,
            max_tool_turns=llm_cfg.coder.max_tool_turns,
            workspace_mcp=workspace_mcp,
        )

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
        **custom_functions
    )
    return builder.build_graph()