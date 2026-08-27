"""Shared validator-runner for the engine and the MCP server.

FILE: snodo/validators/runner.py

The single implementation of "resolve validators → build context → dispatch
via the registry → apply severity caps".  Used by BOTH:

- the engine (`snodo.engine.validators.ValidatorRunner`), and
- the MCP server (`snodo.mcp.server.CoreToolHandler.handle_validate_task`).

Do not fork this logic into a second implementation.
"""

from __future__ import annotations

import copy
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from snodo.compiler.models import Protocol, Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.infrastructure.config import DEFAULT_MODEL
from snodo.validators.context import ValidatorContext


def resolve_validators(
    protocol: Protocol, mode_id: str, phase: str = "pre_execute"
) -> Tuple[Optional[Any], List[Validator]]:
    """Resolve the validators for *mode_id* filtered to *phase*.

    Returns (mode, validators).  *mode* is None if the mode does not exist.
    """
    mode = protocol.get_mode(mode_id)
    if not mode:
        return None, []
    validators: List[Validator] = [
        v for v in (
            protocol.get_validator(vid) for vid in mode.validators
        )
        if v is not None and v.evaluation_phase == phase
    ]
    return mode, validators




def extract_cited_indices(justification: str, total_criteria: int) -> List[int]:
    """Find 1-based criteria indices cited in justification text.

    Supports patterns like 'criterion 3', 'criteria 1 and 2', 'criterion #3',
    'rule 2', 'item 1'.
    """
    if not justification or total_criteria <= 0:
        return []

    cited = set()
    pattern = r'\b(?:criterion|criteria|rule|item)s?\s*(?:#?\s*\d+\s*(?:,?\s*(?:and|or)?\s*#?\s*\d+)*)'
    matches = re.findall(pattern, justification, re.IGNORECASE)
    for m in matches:
        nums = [int(n) for n in re.findall(r'\d+', m)]
        for n in nums:
            if 1 <= n <= total_criteria:
                cited.add(n)

    if not cited:
        nums = [int(n) for n in re.findall(r'\b(?:criterion|criteria|rule|item)?\s*#?\s*(\d+)\b', justification, re.IGNORECASE)]
        for n in nums:
            if 1 <= n <= total_criteria:
                cited.add(n)

    return sorted(list(cited))


def enrich_result_with_criteria(
    result: ValidatorResult, criteria: Optional[List[str]]
) -> ValidatorResult:
    """Enrich ValidatorResult with legible cited criteria text."""
    if not result or not criteria:
        return result

    total_criteria = len(criteria)
    cited_indices = extract_cited_indices(result.justification, total_criteria)
    if not cited_indices:
        return result

    cited_list: List[str] = []
    justification = result.justification

    for idx in cited_indices:
        criterion_text = criteria[idx - 1].strip()
        cited_list.append(f"[Criterion {idx}] {criterion_text}")

        excerpt = criterion_text[:100] + "..." if len(criterion_text) > 100 else criterion_text
        if excerpt[:30] not in justification:
            # Match 'criterion 3', 'criteria 3', or standalone '3' in list
            pattern = rf'(\b(?:criterion|criteria|rule|item)\s*#?\s*{idx}\b|\b{idx}\b)'
            justification = re.sub(
                pattern,
                rf"\1 ('{excerpt}')",
                justification,
                flags=re.IGNORECASE,
                count=1,
            )

    return ValidatorResult(
        validator_id=result.validator_id,
        severity=result.severity,
        justification=justification,
        error=result.error,
        cited_criteria=cited_list,
    )


def dispatch_validator(
    v: Validator, context: ValidatorContext, reg: Any
) -> ValidatorResult:
    """Resolve *v*'s class via the registry and evaluate it.

    Never raises — failures become ``error=True`` ValidatorResults (which the
    policy evaluator maps to ``validator_error`` / fail-closed).
    """
    always_register = {"quality", "protocol"}
    cls = reg.lookup(v.validator_type) if (
        v.criteria or v.validator_type in always_register
    ) else None
    if cls is not None:
        try:
            instance = cls(validator_spec=v)
            result = instance.evaluate(context)
        except Exception as e:  # noqa: BLE001 — validator isolation boundary
            result = ValidatorResult(
                validator_id=v.validator_id,
                severity="blocker",
                justification=f"Validator error: {e}",
                error=True,
            )
        return enrich_result_with_criteria(result, v.criteria)

    if context.completion_fn and v.criteria:
        from snodo.validators.llm_validator import LLMValidator

        try:
            instance = LLMValidator(validator_spec=v)
            result = instance.evaluate(context)
        except Exception as e:  # noqa: BLE001
            result = ValidatorResult(
                validator_id=v.validator_id,
                severity="blocker",
                justification=f"LLM validation failed: {e}",
                error=True,
            )
        return enrich_result_with_criteria(result, v.criteria)

    if v.criteria:
        result = ValidatorResult(
            validator_id=v.validator_id,
            severity="blocker",
            justification=f"LLM unavailable for {v.validator_type} validation",
            error=True,
        )
        return enrich_result_with_criteria(result, v.criteria)

    result = ValidatorResult(
        validator_id=v.validator_id,
        severity="warn",
        justification=f"No criteria configured for {v.validator_type} — nothing to evaluate",
    )
    return enrich_result_with_criteria(result, v.criteria)


def run_validators(
    protocol: Protocol,
    validators: List[Validator],
    task: Task,
    phase: str = "pre_execute",
    completion_fn: Any = None,
    default_model: str = DEFAULT_MODEL,
    validator_config: Any = None,
    workspace_mcp: Any = None,
    git_mcp: Any = None,
    current_mode: str = "",
    authorized_decisions: Optional[List[str]] = None,
    decision_issuer: Any = None,
    session_id: str = "",
    audit_log: Any = None,
    dispatch_fn: Any = None,
    progress_cb: Any = None,
    artifacts: Optional[List[str]] = None,
) -> Tuple[List[ValidatorResult], Dict[str, str]]:
    """Run a list of validators against a task and return ordered results.

    This is the shared multi-validator runner.  *dispatch_fn* defaults to
    :func:`dispatch_validator`; the engine passes its own ``_dispatch_one``
    method so tests can monkey-patch it (see tests/engine/test_validator_model_override.py).

    *artifacts* is the list of produced file paths (post-execute only; empty
    for pre-execute).  It is carried on the ValidatorContext so a validator
    that judges the produced work (e.g. the acceptance validator) can see what
    was produced.

    Returns (results, cap_originals) where ``cap_originals`` maps a validator
    id to its original (pre-cap) severity when a severity_cap was applied.

    Results carrying ``error=True`` are never capped: a validator crash is an
    operational fault, not a severity judgement, and capping it would drop the
    error flag and bypass the fail-closed ``error_count > 0 → HALT`` path in
    ``PolicyEvaluator.evaluate``.
    """
    from snodo.validators.registry import _default_registry as reg

    if dispatch_fn is None:
        dispatch_fn = dispatch_validator

    mode_obj = protocol.get_mode(current_mode)
    _vcfg = validator_config
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
            ], {}

    context = ValidatorContext(
        task=task,
        current_mode=mode_obj,
        protocol=protocol,
        artifacts=list(artifacts or []),
        audit_log=audit_log,
        mode_name=mode_obj.name if mode_obj else "",
        mode_tools=list(mode_obj.tools) if mode_obj else [],
        mode_transitions=dict(mode_obj.transitions) if mode_obj else {},
        mode_validator_refs=list(mode_obj.validators) if mode_obj else [],
        completion_fn=completion_fn,
        model=default_model,
        working_directory=str(Path.cwd()) if not workspace_mcp
        else str(getattr(workspace_mcp, "project_root", Path.cwd())),
        workspace_mcp=workspace_mcp,
        git_mcp=git_mcp,
        phase=phase,
        max_tokens=_vcfg.max_tokens,
        max_tool_turns=_vcfg.max_tool_turns,
        job_id=session_id,
        task_id=task.id,
        progress_callback=progress_cb,
    )

    # Resolve set_model overrides once per pass
    overrides: Dict[str, str] = {}
    if authorized_decisions and decision_issuer:
        verified = decision_issuer.find_set_model_overrides(authorized_decisions)
        for payload in verified:
            scope = payload.get("scope", "")
            if scope.startswith("validator:"):
                vid = scope.split(":", 1)[1]
                overrides[vid] = payload.get("proposed_model", "")

    results_by_id: Dict[str, ValidatorResult] = {}
    cap_originals: Dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=min(len(validators), 4)) as executor:
        futures = {}
        for v in validators:
            override_model = overrides.get(v.validator_id)
            effective_model = override_model or v.model or default_model or DEFAULT_MODEL
            ctx = copy.copy(context)
            ctx.model = effective_model
            future = executor.submit(dispatch_fn, v, ctx, reg)
            futures[future] = v.validator_id

        for future in as_completed(futures):
            vid = futures[future]
            try:
                result = future.result()
            except Exception as e:  # noqa: BLE001
                result = ValidatorResult(
                    validator_id=vid,
                    severity="blocker",
                    justification=f"Validator error: {e}",
                    error=True,
                )
            if result is not None:
                if progress_cb is not None:
                    try:
                        progress_cb(vid, result)
                    except Exception:
                        pass
                v_obj = next((v for v in validators if v.validator_id == vid), None)
                is_recovery = (getattr(task, "depth", 0) > 0 or bool(getattr(task, "prior_failures", None)))
                if (
                    phase == "pre_execute"
                    and is_recovery
                    and result.severity in ("warn", "blocker")
                    and not getattr(result, "error", False)
                ):
                    original_severity = result.severity
                    cap_originals[result.validator_id] = original_severity
                    result = ValidatorResult(
                        validator_id=result.validator_id,
                        severity="pass",
                        justification=f"[Pre-execute recovery finding ({original_severity}): non-blocking evidence for coder] {result.justification}",
                        cited_criteria=result.cited_criteria,
                    )
                    if audit_log is not None:
                        _cap_data = {
                            "validator_id": result.validator_id,
                            "original": original_severity,
                            "capped": "pass",
                            "reason": "pre_execute_recovery_tree_state",
                        }
                        if session_id:
                            _cap_data["session_id"] = session_id
                        audit_log.append_event("severity_cap_applied", _cap_data)
                elif (
                    v_obj is not None
                    and v_obj.severity_cap is not None
                    and not getattr(result, "error", False)
                ):
                    from snodo.compiler.models import Severity

                    if Severity(result.severity) > v_obj.severity_cap:
                        original_severity = result.severity
                        result = ValidatorResult(
                            validator_id=result.validator_id,
                            severity=v_obj.severity_cap.value,
                            justification=result.justification,
                        )
                        cap_originals[result.validator_id] = original_severity
                        if audit_log is not None:
                            _cap_data = {
                                "validator_id": result.validator_id,
                                "original": original_severity,
                                "capped": result.severity,
                            }
                            if session_id:
                                _cap_data["session_id"] = session_id
                            audit_log.append_event("severity_cap_applied", _cap_data)
                results_by_id[vid] = result

    results = [results_by_id[v.validator_id] for v in validators]
    return results, cap_originals


def resolve_model_for_role(config: dict, role: str, fallback: str) -> str:
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


def build_completion_fn(model: str, base_fn: Any) -> Any:
    """Build a ``functools.partial`` of *base_fn* bound to *model*.

    If the model's provider has a ``base_url`` configured, ``api_base`` is
    also bound so the call routes to the correct endpoint.
    """
    import functools

    from snodo.config import ConfigManager

    kwargs: Dict[str, Any] = {"model": model}
    api_base = ConfigManager.resolve_api_base(model)
    if api_base:
        kwargs["api_base"] = api_base
    return functools.partial(base_fn, **kwargs)


def resolve_validator_completion() -> Tuple[Any, str, Any]:
    """Resolve (completion_fn, validator_model, validator_config) for validators.

    Mirrors the engine's GraphBuilder resolution but without a coder: the base
    completion function is ``litellm.completion``.  Raises on config errors so
    the caller can surface ``validator_error`` (not a stub pass).
    """
    from litellm import completion as litellm_completion

    from snodo.config import ConfigManager, provider_env
    from snodo.infrastructure.config import load_llm_config

    config = ConfigManager().load()
    validator_model = resolve_model_for_role(config, "validator", DEFAULT_MODEL)
    validator_config = load_llm_config().validator

    with provider_env(validator_model):
        completion_fn = build_completion_fn(validator_model, litellm_completion)

    return completion_fn, validator_model, validator_config


def classify_outcome(results: List[ValidatorResult], decision: Any) -> str:
    """Map (validator results, policy decision) to one of the four statuses:

    ``pass`` | ``escalate`` | ``blocker`` | ``validator_error``
    """
    from snodo.engine.policy import PolicyAction

    if decision.action in (PolicyAction.PROCEED, PolicyAction.PROCEED_WITH_LOG):
        return "pass"
    if decision.action == PolicyAction.ESCALATE:
        return "escalate"
    # HALT — fail closed; distinguish validator errors from genuine blockers.
    if any(getattr(r, "error", False) for r in results):
        return "validator_error"
    return "blocker"
