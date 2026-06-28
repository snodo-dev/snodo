from typing import Dict, Any, List
from snodo.engine.state import LoopStage, LoopState, _build_audit_results
from snodo.core.interfaces import ValidatorResult, ExecutionError
from snodo.engine.policy import PolicyAction, policy_decision_to_dict


class ValidationNodeMixin:
    """Mixin providing validation node capabilities to GraphBuilder."""

    def _build_pending_disagreement(
        self, loop_state: LoopState, phase: str, results: List[ValidatorResult], decision: Any
    ) -> Dict[str, Any]:
        """Construct the pending_disagreement dict and audit it."""
        pending_disagreement = {
            "phase": phase,
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
            "phase": phase,
            "task_ref": loop_state.task.id,
            "policy": self.protocol.disagreement_policy.value,
            "validator_results": pending_disagreement["validator_results"],
            "policy_decision": pending_disagreement["policy_decision"],
        })
        return pending_disagreement

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
            has_errors = any(getattr(r, 'error', False) for r in results)
            loop_state.halt_type = "validator_error" if has_errors else "blocked"
        elif decision.action == PolicyAction.ESCALATE:
            loop_state.is_blocked = True
            loop_state.halt_type = "escalated"
            loop_state.pending_disagreement = self._build_pending_disagreement(
                loop_state, "pre_execute", results, decision
            )
            outcome = "escalated"

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
            has_errors = any(getattr(r, 'error', False) for r in results)
            if has_errors:
                loop_state.is_blocked = True
                loop_state.halt_type = "validator_error"
                loop_state.constraint_violations.append(
                    "Validator error: " + decision.justification
                )
                post_outcome = "blocked"
            elif self._is_recoverable(loop_state, results):
                self._spawn_recovery_subtask(loop_state, results, decision)
                post_outcome = "recovery"
            else:
                loop_state.is_blocked = True
                loop_state.halt_type = "blocked"
                loop_state.constraint_violations.append(
                    "Post-execute validation failed: " + decision.justification
                )
                post_outcome = "blocked"
        elif decision.action == PolicyAction.ESCALATE:
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
            "results": _build_audit_results(post_validators, results),
            "outcome": post_outcome,
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

