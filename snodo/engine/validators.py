"""Validator dispatch and execution for protocol validation."""

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
        )

        results = []
        for v in validators:
            effective_model = v.model or self._default_model or DEFAULT_MODEL
            context.model = effective_model
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
                    severity="warn",
                    justification=f"Validator error: {e}",
                )

        if context.completion_fn and v.criteria:
            from snodo.validators.llm_validator import LLMValidator
            try:
                instance = LLMValidator(validator_spec=v)
                return instance.evaluate(context)
            except Exception as e:
                return ValidatorResult(
                    validator_id=v.validator_id,
                    severity="warn",
                    justification=f"LLM validation failed: {e}",
                )

        if v.criteria:
            return ValidatorResult(
                validator_id=v.validator_id,
                severity="warn",
                justification=f"LLM unavailable for {v.validator_type} validation",
            )

        return ValidatorResult(
            validator_id=v.validator_id,
            severity="pass",
            justification=f"Stub validation for {v.validator_type}",
        )
