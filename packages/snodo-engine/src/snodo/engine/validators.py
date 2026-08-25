"""Validator dispatch and execution for protocol validation.

FILE: snodo/engine/validators.py

Thin adapter over the shared runner in ``snodo.validators.runner``.  The
multi-validator loop, context construction, and severity-cap handling all
live in ``run_validators`` (single implementation).  ``_dispatch_one`` is
kept as a method so tests can monkey-patch it (see
tests/engine/test_validator_model_override.py).
"""

from typing import Any, Callable, List, Optional

from snodo.compiler.models import Protocol, Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.tools.shell import ShellMCP
from snodo.validators.context import ValidatorContext
from snodo.validators.runner import dispatch_validator, resolve_validators as _resolve, run_validators as _run


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
        self.last_cap_originals: dict = {}

    def resolve_validators(
        self, mode_id: str, phase: str = "pre_execute"
    ) -> tuple:
        return _resolve(self.protocol, mode_id, phase)

    def run(
        self,
        task: Task,
        validators: List[Validator],
        shell_mcp: Optional[ShellMCP],
        current_mode: str = "",
        phase: str = "",
        authorized_decisions: Optional[List[str]] = None,
        decision_issuer: Any = None,
        progress_cb: Any = None,
        artifacts: Optional[List[str]] = None,
    ) -> List[ValidatorResult]:
        results, cap_originals = _run(
            protocol=self.protocol,
            validators=validators,
            task=task,
            phase=phase,
            completion_fn=self._completion_fn,
            default_model=self._default_model,
            validator_config=self._validator_config,
            workspace_mcp=self.workspace_mcp,
            git_mcp=self.git_mcp,
            current_mode=current_mode,
            authorized_decisions=authorized_decisions,
            decision_issuer=decision_issuer,
            session_id=getattr(self, "_session_id", ""),
            audit_log=self._audit_log,
            dispatch_fn=self._dispatch_one,
            progress_cb=progress_cb,
            artifacts=artifacts,
        )
        self.last_cap_originals = cap_originals
        return results

    def _dispatch_one(
        self, v: Validator, context: ValidatorContext, reg: Any
    ) -> ValidatorResult:
        return dispatch_validator(v, context, reg)
