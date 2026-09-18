from pathlib import PurePosixPath
from typing import Dict, Any, List
from snodo.engine.state import LoopStage, LoopState, _build_audit_results
from snodo.core.interfaces import ValidatorResult, ExecutionError, NoFileOperationsError, result_record
from snodo.coders.base import (
    AdapterError,
    CoderTimeoutError,
    CoderUnavailableError,
    SnodoMutationError,
    TurnBudgetExhausted,
)
from snodo.infrastructure.tokens import TokenStoreError
from snodo.engine.policy import PolicyAction, policy_decision_to_dict
from snodo.engine.nodes.writeback import _coder_registry_name


class ValidationNodeMixin:
    """Mixin providing validation node capabilities to GraphBuilder."""

    def _build_pending_disagreement(
        self, loop_state: LoopState, phase: str, results: List[ValidatorResult], decision: Any
    ) -> Dict[str, Any]:
        """Construct the pending_disagreement dict and audit it."""
        pending_disagreement = {
            "phase": phase,
            "policy": self.protocol.disagreement_policy.value,
            "validator_results": [result_record(r) for r in results],
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

        self._progress(
            f"  Validating (pre-execute): {', '.join(v.validator_id for v in validators)}"
        )

        results = self.validator_fn(loop_state.task, validators, self.shell_mcp,
                                    current_mode=loop_state.current_mode,
                                    phase="pre_execute",
                                    authorized_decisions=getattr(self, '_authorized_decisions', []),
                                    decision_issuer=self._decision_issuer,
                                    progress_cb=self._progress,
                                    verdict_cb=self._validator_verdict_cb)
        loop_state.validation_results = results

        is_recovery = (loop_state.task.depth > 0 or bool(loop_state.task.prior_failures))
        decision = self.policy_evaluator.evaluate(
            results,
            self.protocol.disagreement_policy,
            "pre_execute",
            decision_records=getattr(self, '_decision_records', []),
            task_ref=loop_state.task.id,
            is_recovery=is_recovery,
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
                if r.severity not in (None, "pass") and self._judges_spec(r.validator_id)
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

            loop_state.pending_disagreement = self._build_pending_disagreement(
                loop_state, "pre_execute", results, decision
            )

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

        # Capture the HEAD sha BEFORE the coder runs. Post-execute judges diff
        # base_ref..HEAD; without this anchor an adapter that returns file
        # operations but does not commit leaves HEAD pointing at the previous
        # unrelated commit, and HEAD~1..HEAD judges that and passes (Fixes #103).
        if loop_state.base_ref is None and self.git_mcp is not None:
            try:
                loop_state.base_ref = self.git_mcp.get_head_sha()
            except Exception:
                loop_state.base_ref = None

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
                self._auto_write_failure_context(loop_state, [])
                return self._state_to_dict(loop_state)
            self._last_execution_writes = []
            self._last_execution_reads = {"files": [], "directories": []}
            self._last_output_tail = ""
            # A timeout is a per-run fact. Reset it here, with the other
            # per-attempt facts, so a halt payload built for THIS run can never
            # carry a previous attempt's timeout or output (Fixes #281).
            self._last_timed_out = False
            self._last_timeout_seconds = None
            self._last_timeout_tail = ""
            # The turn-budget bound is a per-run fact too: reset it with the
            # other facts so a halt payload cannot carry a previous attempt's
            # bounded outcome (Fixes #282).
            self._last_turn_budget_exhausted = False
            self._last_existing_work_base_ref = None
            self._last_coder_report = None
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
                loop_state.metadata["attempt_written_files"] = list(self._last_execution_writes)
                loop_state.metadata["attempt_read_files"] = dict(self._last_execution_reads)
                if getattr(self, "_last_findings", None) is not None:
                    loop_state.metadata["findings"] = self._last_findings
                if getattr(self, "_last_coder_report", None) is not None:
                    loop_state.metadata["coder_report"] = self._last_coder_report.model_dump()
                    if "findings" not in loop_state.metadata and getattr(self._last_coder_report, "findings", None) is not None:
                        loop_state.metadata["findings"] = self._last_coder_report.findings
                if getattr(self, "_last_output_tail", ""):
                    loop_state.metadata["output_tail"] = getattr(self, "_last_output_tail", "")
                if getattr(self, "_last_timed_out", False):
                    loop_state.metadata["timed_out"] = True
                    loop_state.metadata["timeout_seconds"] = getattr(self, "_last_timeout_seconds", None)
                    if getattr(self, "_last_timeout_tail", ""):
                        loop_state.metadata["output_tail"] = getattr(self, "_last_timeout_tail", "")
                    self._audit("coder_timed_out", {
                        "op": "coder_timed_out",
                        "task_ref": loop_state.task.id,
                        "mode": loop_state.current_mode,
                        "timeout_seconds": getattr(self, "_last_timeout_seconds", None),
                        "artifacts_count": len(artifacts),
                        "output_tail": getattr(self, "_last_timeout_tail", ""),
                    })
                    # A run that ran out of time is worth knowing about even
                    # when its work passes: say so rather than letting it
                    # proceed silently (Fixes #281).
                    self._progress(
                        "  Coder timed out after "
                        f"{getattr(self, '_last_timeout_seconds', None)}s; "
                        f"judging the {len(artifacts)} artifact(s) it produced."
                    )
                if getattr(self, "_last_turn_budget_exhausted", False):
                    # The run hit its turn bound but its work was recoverable
                    # from the task branch and is about to be judged. Record the
                    # bound so the payload says a run ran out of turns even when
                    # the work passes — the same "do not proceed silently"
                    # obligation the clock bound carries (Fixes #282).
                    loop_state.metadata["turn_budget_exhausted"] = True
                    self._audit("coder_turn_budget_exhausted", {
                        "op": "coder_turn_budget_exhausted",
                        "task_ref": loop_state.task.id,
                        "mode": loop_state.current_mode,
                        "artifacts_count": len(artifacts),
                    })
                    self._progress(
                        "  Coder exhausted its turn budget; judging the "
                        f"{len(artifacts)} artifact(s) recovered from the task "
                        "branch."
                    )
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
                self._auto_write_failure_context(loop_state, [])
                return self._state_to_dict(loop_state)
            except TurnBudgetExhausted as e:
                # The coder burned its full turn budget without submitting. A
                # bounded, anticipated outcome — not a crash and not a
                # validator verdict — so it gets its own terminal halt instead
                # of ``internal_error``. It is the same kind of thing as the
                # clock bound below: the coder ran out of turns to *submit*, not
                # to write, so the executor has already asked the task-branch
                # probe whether the work it produced exists and, finding none,
                # lets this exception through. It maps to the operational
                # ``environment_error`` (ADR 015/#282), never a blocker verdict
                # about work no judge saw, and recovery must NOT spawn against
                # it (retrying a turn-budget exhaustion cannot converge).
                loop_state.is_blocked = True
                loop_state.halt_type = "turn_budget_exhausted"
                loop_state.constraint_violations.append(str(e))
                loop_state.metadata["turn_budget_exhausted"] = True
                loop_state.metadata["post_validation"] = {
                    "outcome": "skipped",
                    "reason": str(e),
                }
                self._audit("coder_turn_budget_exhausted", {
                    "op": "coder_turn_budget_exhausted",
                    "task_ref": loop_state.task.id,
                    "error": str(e),
                })
                self._auto_write_failure_context(loop_state, [])
                return self._state_to_dict(loop_state)
            except CoderTimeoutError as e:
                # A run that ended on the clock. This is an operational fact
                # about the run, not a finding about the code: the coder was
                # invoked and did not finish in time — the same kind of thing as
                # a coder that could not be invoked at all — so it halts under
                # the raw AND canonical ``environment_error`` (ADR 015), never
                # the ``execution_error``/blocker family. The produced work (if
                # any) was already carried into post-execute validation by the
                # executor; here nothing recoverable was left, so no blocker
                # verdict is recorded and no ``task_failure`` context is
                # written. The timeout itself is recorded, because a run that
                # ran out of time is worth knowing about even when it did not
                # block (Fixes #281).
                loop_state.is_blocked = True
                loop_state.halt_type = "environment_error"
                loop_state.constraint_violations.append(str(e))
                loop_state.metadata["timed_out"] = True
                loop_state.metadata["timeout_seconds"] = (
                    getattr(e, "timeout_seconds", None)
                    or getattr(self, "_last_timeout_seconds", None)
                )
                if getattr(self, "_last_timeout_tail", ""):
                    loop_state.metadata["output_tail"] = getattr(self, "_last_timeout_tail", "")
                loop_state.metadata["post_validation"] = {
                    "outcome": "skipped",
                    "reason": str(e),
                }
                if getattr(self, "_last_coder_report", None) is not None:
                    loop_state.metadata["coder_report"] = self._last_coder_report.model_dump()
                self._audit("coder_timed_out", {
                    "op": "coder_timed_out",
                    "task_ref": loop_state.task.id,
                    "mode": loop_state.current_mode,
                    "timeout_seconds": loop_state.metadata["timeout_seconds"],
                    "artifacts_count": 0,
                    "output_tail": getattr(self, "_last_timeout_tail", ""),
                })
                return self._state_to_dict(loop_state)
            except CoderUnavailableError as e:
                # The environment, not the task: the coder's binary (or the
                # container runtime it needs) cannot be invoked in the process
                # that ran. Halt under the raw AND canonical
                # ``environment_error`` (ADR 015) — not the
                # ``execution_error``/blocker family. Nothing about the task
                # blocked, so no blocker verdict is recorded and no
                # ``task_failure`` context is written: a retry after the
                # install is a fresh execution of the unchanged spec.
                loop_state.is_blocked = True
                loop_state.halt_type = "environment_error"
                loop_state.constraint_violations.append(str(e))
                loop_state.metadata["post_validation"] = {
                    "outcome": "skipped",
                    "reason": str(e),
                }
                if getattr(self, "_last_coder_report", None) is not None:
                    loop_state.metadata["coder_report"] = self._last_coder_report.model_dump()
                self._audit("coder_unavailable", {
                    "op": "coder_unavailable",
                    "task_ref": loop_state.task.id,
                    "mode": loop_state.current_mode,
                    "binary": e.binary,
                    "error": str(e),
                })
                return self._state_to_dict(loop_state)
            except AdapterError as e:
                # The coder backend itself failed — a provider unreachable, an
                # LLM call that errored, a session that could not be established.
                # This is an operational fault about the run, not a verdict
                # about the code (ADR 015): it halts under the raw
                # ``execution_error`` type (canonical ``environment_error``).
                # Nothing about the task blocked, so no blocker verdict is
                # recorded and no ``task_failure`` context is written: a retry
                # after the operational fault is resolved is a fresh execution
                # of the unchanged spec (Fixes #301).
                loop_state.is_blocked = True
                loop_state.halt_type = "execution_error"
                loop_state.constraint_violations.append(str(e))
                loop_state.metadata["post_validation"] = {
                    "outcome": "skipped",
                    "reason": str(e),
                }
                output_tail = getattr(self, "_last_output_tail", "")
                if output_tail:
                    loop_state.metadata["output_tail"] = output_tail
                if getattr(self, "_last_coder_report", None) is not None:
                    loop_state.metadata["coder_report"] = self._last_coder_report.model_dump()
                self._audit("execution_failed", {
                    "op": "execution_failed",
                    "task_ref": loop_state.task.id,
                    "error": str(e),
                })
                return self._state_to_dict(loop_state)
            except NoFileOperationsError as e:
                # The coder completed successfully (exit 0) but produced no
                # file operations or changes. This is a nameable blocker halt
                # (``no_file_operations``), not an engine failure
                # (``internal_error``): the operator is told to revise the spec
                # or fix the coder configuration (Fixes #203).
                loop_state.is_blocked = True
                loop_state.halt_type = "no_file_operations"
                loop_state.constraint_violations.append(str(e))
                loop_state.metadata["post_validation"] = {
                    "outcome": "skipped",
                    "reason": str(e),
                }
                output_tail = getattr(self, "_last_output_tail", "")
                if output_tail:
                    loop_state.metadata["output_tail"] = output_tail
                if getattr(self, "_last_coder_report", None) is not None:
                    loop_state.metadata["coder_report"] = self._last_coder_report.model_dump()
                self._audit("no_file_operations", {
                    "op": "no_file_operations",
                    "task_ref": loop_state.task.id,
                    "error": "Coder produced no file operations",
                })
                self._auto_write_failure_context(loop_state, [])
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
                if getattr(self, "_last_coder_report", None) is not None:
                    loop_state.metadata["coder_report"] = self._last_coder_report.model_dump()
                self._audit("execution_failed", {
                    "op": "execution_failed",
                    "task_ref": loop_state.task.id,
                    "error": str(e),
                })
                self._auto_write_failure_context(loop_state, [])
                return self._state_to_dict(loop_state)

            loop_state.artifacts.extend(artifacts)

            # The coder produced no operations this attempt because the task
            # branch already carried the work — an earlier attempt committed it
            # and then the run failed afterwards. This is NOT "coder produced
            # nothing": the work exists, sitting on the branch this run is
            # executing. Report that plainly and point the post-execute judges
            # at the branch base so they diff base_ref..HEAD (the committed
            # work) instead of this no-op attempt's empty HEAD..HEAD range, and
            # so the head_not_moved guard below does not mistake the
            # intentionally-unchanged HEAD for a claimed-but-missing commit.
            existing_base_ref = self._last_existing_work_base_ref
            if existing_base_ref:
                self._last_existing_work_base_ref = None
                loop_state.base_ref = existing_base_ref
                work_files = sorted({str(a) for a in loop_state.artifacts})
                self._progress(
                    "  Work already present on the task branch: an earlier "
                    f"attempt committed {len(work_files)} file(s) before the run "
                    "failed. Validating the existing work instead of reporting "
                    "no_file_operations."
                )
                self._audit("work_already_present", {
                    "op": "work_already_present",
                    "task_ref": loop_state.task.id,
                    "base_ref": existing_base_ref,
                    "artifacts_count": len(work_files),
                    "files": work_files,
                })

            # The coder produced observable work — the git review channel must
            # reflect it. If the executor returned artifacts but HEAD did not
            # move, the adapter claimed a commit it did not make: the
            # post-execute judges would diff base_ref..HEAD = an empty range
            # (or worse, review the previous unrelated commit) and pass. This
            # is a nameable fault, not the generic blocked path (Fixes #103).
            if (
                loop_state.artifacts
                and loop_state.base_ref
                and self.git_mcp is not None
            ):
                try:
                    current_head = self.git_mcp.get_head_sha()
                except Exception:
                    current_head = None
                if current_head is not None and current_head == loop_state.base_ref:
                    commit_reason = getattr(self, "_last_commit_reason", None) or "unknown"
                    loop_state.is_blocked = True
                    loop_state.halt_type = "head_not_moved"
                    loop_state.constraint_violations.append(
                        "The coder reported file operations but HEAD did not "
                        f"move (commit failure: {commit_reason}): no commit was created, so post-execute "
                        "validators would review the previous commit instead of "
                        "the produced change. The adapter claimed a commit it "
                        "did not make (skip_engine_commit and skip_workspace_write "
                        "opt out of the engine's commit mechanism, not the "
                        "obligation that produced work be committed)."
                    )
                    loop_state.metadata["post_validation"] = {
                        "outcome": "skipped",
                        "reason": "head_not_moved",
                        "commit_reason": commit_reason,
                    }
                    self._audit("head_not_moved", {
                        "op": "head_not_moved",
                        "task_ref": loop_state.task.id,
                        "base_ref": loop_state.base_ref,
                        "artifacts_count": len(loop_state.artifacts),
                        "commit_reason": commit_reason,
                    })
                    self._auto_write_failure_context(loop_state, [])
                    return self._state_to_dict(loop_state)

            # Housekeeping: clear the in-memory slot (enforcement is the store).
            loop_state.validation_token = None
            self._audit("token_consumed", {
                "op": "token_consumed",
                "task_ref": loop_state.task.id,
            })

        coder_obj = getattr(self, "coder", None)
        coder_name = _coder_registry_name(coder_obj)
        if hasattr(coder_obj, "_bare_model"):
            bare = coder_obj._bare_model()
            coder_model = bare if bare else None
        else:
            coder_model = getattr(coder_obj, "model", None)

        judging_model = getattr(self, "_default_model", None)

        dispatch_audit = {
            "op": "dispatch",
            "task_ref": loop_state.task.id,
            "token_id": loop_state.task.id,
            "mode": loop_state.current_mode,
            "coder": coder_name,
            "coder_model": coder_model,
            "judging_model": judging_model,
            "artifacts_count": len(loop_state.artifacts),
        }
        if loop_state.metadata.get("timed_out"):
            dispatch_audit["timed_out"] = True
            dispatch_audit["timeout_seconds"] = loop_state.metadata.get("timeout_seconds")
        self._audit("dispatch", dispatch_audit)

        # Track execution in messages for agent memory
        artifact_summary = ", ".join(loop_state.artifacts) if loop_state.artifacts else "none"
        loop_state.messages.append({
            "role": "assistant",
            "content": f"Executed task '{loop_state.task.spec}' in mode "
                       f"'{loop_state.current_mode}'. Artifacts: {artifact_summary}."
        })

        return self._state_to_dict(loop_state)

    def _run_post_validators(
        self, loop_state: LoopState, validators: List[Any]
    ) -> List[ValidatorResult]:
        """Run *validators* for the post-execute phase and return their results."""
        return self.validator_fn(
            loop_state.task, validators, self.shell_mcp,
            current_mode=loop_state.current_mode,
            phase="post_execute",
            authorized_decisions=getattr(self, '_authorized_decisions', []),
            decision_issuer=self._decision_issuer,
            progress_cb=self._progress,
            verdict_cb=self._validator_verdict_cb,
            artifacts=list(loop_state.artifacts),
            base_ref=loop_state.base_ref,
        )

    def _protected_path_result(self, loop_state: LoopState) -> Any:
        """Return a blocker when the task branch changed a declared path."""
        protected = [
            str(PurePosixPath(path.replace("\\", "/"))).strip("/")
            for path in getattr(self.protocol, "protected_paths", [])
            if str(path).strip()
        ]
        if not protected or not loop_state.base_ref or self.git_mcp is None:
            return None
        try:
            changed = self.git_mcp.changed_paths_between_refs(
                loop_state.base_ref, self.git_mcp.get_head_sha()
            )
        except Exception as exc:
            self._audit("protected_paths_unchecked", {
                "op": "protected_paths_unchecked",
                "task_ref": loop_state.task.id,
                "error": str(exc),
            })
            return None

        changed_normalized = [str(PurePosixPath(path.replace("\\", "/"))) for path in changed]
        touched = sorted({
            declared
            for declared in protected
            if any(
                changed_path == declared or changed_path.startswith(declared + "/")
                for changed_path in changed_normalized
            )
        })
        if not touched:
            return None
        return ValidatorResult(
            validator_id="protected_paths",
            severity="blocker",
            justification=(
                "Protected path changed in the task branch: " + ", ".join(touched)
            ),
        )

    def _detect_validator_contradictions(
        self, loop_state: LoopState, results: List[ValidatorResult]
    ) -> None:
        """Flag a read-only acceptance pass that contradicts a failing gate.

        A quality/test-runner failure and an acceptance judge claiming the work
        passes cannot both stand; the mechanical failure wins.
        """
        quality_failing = [
            r for r in results
            if getattr(r, "error", False) or r.severity in ("warn", "blocker")
        ]
        acceptance_passing = [
            r for r in results
            if r.validator_id == "acceptance" and r.severity == "pass"
        ]
        if not (quality_failing and acceptance_passing):
            return
        q_res = quality_failing[0]
        for a_res in acceptance_passing:
            a_just = a_res.justification or ""
            self._audit("validator_contradiction_detected", {
                "op": "validator_contradiction_detected",
                "task_ref": loop_state.task.id,
                "execution_validator": q_res.validator_id,
                "execution_justification": q_res.justification,
                "acceptance_justification": a_just,
            })
            if any(kw in a_just.lower() for kw in ("check", "test", "npm", "pytest", "build", "passes", "met")):
                a_res.severity = "blocker"
                a_res.justification = (
                    f"[CONTRADICTION DETECTED: execution validator '{q_res.validator_id}' failed "
                    f"({q_res.justification}). Acceptance claim superseded.] {a_just}"
                )

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
        protected_result = self._protected_path_result(loop_state)
        if not current_mode or not post_validators:
            if protected_result is not None:
                loop_state.is_blocked = True
                loop_state.halt_type = "blocked"
                loop_state.validation_results.append(protected_result)
                loop_state.constraint_violations.append(protected_result.justification)
                loop_state.metadata["post_validation"] = {
                    "outcome": "blocked",
                    "validator_results": [protected_result.model_dump()],
                }
                self._audit("protected_path_blocked", {
                    "op": "protected_path_blocked",
                    "task_ref": loop_state.task.id,
                    "justification": protected_result.justification,
                })
                self._auto_write_failure_context(loop_state, [protected_result])
                return self._state_to_dict(loop_state)
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
        results = self._run_post_validators(loop_state, post_validators)
        if protected_result is not None:
            results.append(protected_result)

        # Detect contradictions between execution validators (quality) and read-only judges (acceptance)
        self._detect_validator_contradictions(loop_state, results)

        # Evaluate policy on post-execute results
        is_recovery = (loop_state.task.depth > 0 or bool(loop_state.task.prior_failures))
        decision = self.policy_evaluator.evaluate(
            results,
            self.protocol.disagreement_policy,
            "post_execute",
            decision_records=getattr(self, '_decision_records', []),
            task_ref=loop_state.task.id,
            is_recovery=is_recovery,
        )

        # Commit the phase's final results once, after any re-judging.
        loop_state.validation_results = loop_state.validation_results + results

        post_outcome = "passed"
        if protected_result is not None:
            # A protected-path finding is terminal and is not eligible for
            # recovery, regardless of the configured disagreement policy.
            loop_state.is_blocked = True
            loop_state.halt_type = "blocked"
            loop_state.constraint_violations.append(protected_result.justification)
            post_outcome = "blocked"
        elif decision.action == PolicyAction.HALT:
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

        if protected_result is not None:
            loop_state.needs_recovery = False
            self._audit("protected_path_blocked", {
                "op": "protected_path_blocked",
                "task_ref": loop_state.task.id,
                "justification": protected_result.justification,
            })

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
