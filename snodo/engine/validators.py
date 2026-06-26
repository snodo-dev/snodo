"""Validator dispatch and execution for protocol validation."""

import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, List, Optional

from snodo.compiler.models import Protocol, Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.infrastructure.config import DEFAULT_MODEL
from snodo.mcp.shell import ShellMCP
from snodo.validators.context import ValidatorContext


class ValidatorRunner:
    """Dispatches validators and builds shared ValidatorContext."""

    def __init__(
        self,
        protocol: Protocol,
        completion_fn: Optional[Callable],
        default_model: str,
        validator_config: Any,
        audit_log: Any,
        workspace_mcp: Any,
        git_mcp: Any,
        session_manager: Any,
    ):
        self.protocol = protocol
        self._completion_fn = completion_fn
        self._default_model = default_model
        self._audit_log = audit_log
        self.workspace_mcp = workspace_mcp
        self.git_mcp = git_mcp
        self._session_manager = session_manager
        self._validator_config = validator_config
        self._session_id: str = ""

    def resolve_validators(
        self, mode_id: str, phase: str = "pre_execute"
    ) -> tuple:
        mode = self.protocol.get_mode(mode_id)
        if not mode:
            return None, []
        validators: List[Validator] = [
            v for v in (
                self.protocol.get_validator(vid)
                for vid in mode.validators
            )
            if v is not None and v.evaluation_phase == phase
        ]
        return mode, validators

    def run(
        self,
        task: Task,
        validators: List[Validator],
        shell_mcp: Optional[ShellMCP],
        current_mode: str = "",
        phase: str = "",
        authorized_decisions: Optional[List[str]] = None,
        decision_issuer: Any = None,
    ) -> List[ValidatorResult]:
        from snodo.validators.registry import _default_registry as reg

        mode_obj = self.protocol.get_mode(current_mode)
        _vcfg = self._validator_config
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
            completion_fn=self._completion_fn,
            model=self._default_model,
            working_directory=str(Path.cwd()) if not self.workspace_mcp
            else str(getattr(self.workspace_mcp, "project_root", Path.cwd())),
            workspace_mcp=self.workspace_mcp,
            git_mcp=self.git_mcp,
            phase=phase,
            max_tokens=_vcfg.max_tokens,
            max_tool_turns=_vcfg.max_tool_turns,
            job_id=getattr(self, "_session_id", ""),
            task_id=task.id,
        )

        # Resolve set_model overrides once per pass
        overrides: dict = {}
        if authorized_decisions and decision_issuer:
            verified = decision_issuer.find_set_model_overrides(
                authorized_decisions,
            )
            for payload in verified:
                scope = payload.get("scope", "")
                if scope.startswith("validator:"):
                    vid = scope.split(":", 1)[1]
                    overrides[vid] = payload.get("proposed_model", "")

        results_by_id: dict[str, ValidatorResult] = {}
        with ThreadPoolExecutor(max_workers=min(len(validators), 4)) as executor:
            futures = {}
            for v in validators:
                override_model = overrides.get(v.validator_id)
                if override_model:
                    effective_model = override_model
                else:
                    effective_model = v.model or self._default_model or DEFAULT_MODEL
                ctx = copy.copy(context)
                ctx.model = effective_model
                future = executor.submit(self._dispatch_one, v, ctx, reg)
                futures[future] = v.validator_id

            for future in as_completed(futures):
                vid = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = ValidatorResult(
                        validator_id=vid,
                        severity="blocker",
                        justification=f"Validator error: {e}",
                        error=True,
                    )
                if result is not None:
                    v_obj = next((v for v in validators if v.validator_id == vid), None)
                    if v_obj is not None and v_obj.severity_cap is not None:
                        from snodo.compiler.models import Severity
                        if Severity(result.severity) > v_obj.severity_cap:
                            result = ValidatorResult(
                                validator_id=result.validator_id,
                                severity=v_obj.severity_cap.value,
                                justification=result.justification,
                            )
                    results_by_id[vid] = result

        # Return in original order
        results = [results_by_id[v.validator_id] for v in validators]
        return results

    def _dispatch_one(
        self, v: Validator, context: ValidatorContext, reg
    ) -> ValidatorResult:
        always_register = {"quality", "protocol"}
        cls = reg.lookup(v.validator_type) if (v.criteria or v.validator_type in always_register) else None
        if cls is not None:
            try:
                instance = cls(validator_spec=v)
                return instance.evaluate(context)
            except Exception as e:
                return ValidatorResult(
                    validator_id=v.validator_id,
                    severity="blocker",
                    justification=f"Validator error: {e}",
                    error=True,
                )

        if context.completion_fn and v.criteria:
            from snodo.validators.llm_validator import LLMValidator
            try:
                instance = LLMValidator(validator_spec=v)
                return instance.evaluate(context)
            except Exception as e:
                return ValidatorResult(
                    validator_id=v.validator_id,
                    severity="blocker",
                    justification=f"LLM validation failed: {e}",
                    error=True,
                )

        if v.criteria:
            return ValidatorResult(
                validator_id=v.validator_id,
                severity="blocker",
                justification=f"LLM unavailable for {v.validator_type} validation",
                error=True,
            )

        return ValidatorResult(
            validator_id=v.validator_id,
            severity="warn",
            justification=f"No criteria configured for {v.validator_type} — nothing to evaluate",
        )
