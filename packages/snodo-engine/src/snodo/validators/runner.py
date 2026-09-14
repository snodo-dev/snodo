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
import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from snodo.compiler.models import Protocol, Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.infrastructure.config import DEFAULT_MODEL
from snodo.validators.change import build_change_context
from snodo.validators.context import ValidatorContext
from snodo.validators.verdict_cache import compute_verdict_key

logger = logging.getLogger(__name__)


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
    if result.severity is None:
        # No verdict — nothing was cited, and the numeric fallback in
        # extract_cited_indices could otherwise turn "after 20 turns" into a
        # fabricated criterion citation (Fixes #252).
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
        abstention_reason=getattr(result, "abstention_reason", None),
        examined=getattr(result, "examined", None),
        unexamined_tools=getattr(result, "unexamined_tools", None),
        last_words=getattr(result, "last_words", None),
        skipped=getattr(result, "skipped", False),
        reused=getattr(result, "reused", False),
    )


# ---------------------------------------------------------------------------
# Verdict cache (#246)
# ---------------------------------------------------------------------------

#: Sentinel types that must never be stored as a verdict: an abstention, an
#: error, and a pass whose gate was skipped are not judgements.  Persisting
#: one would turn the absence of a judgement into a durable claim that one
#: was made — the exact defect the abstention representation exists to
#: prevent.
def _is_cacheable_verdict(result: Any) -> bool:
    """Return True only for a genuine, freshly-computed verdict."""
    if result is None:
        return False
    if getattr(result, "error", False):
        return False
    if getattr(result, "severity", None) is None:
        return False
    if getattr(result, "skipped", False):
        return False
    return True


def _spec_subject(context: ValidatorContext) -> str:
    """Digest the specification a single-completion judge reads."""
    spec = getattr(getattr(context, "task", None), "spec", "") or ""
    return "spec:" + hashlib.sha256(spec.encode("utf-8")).hexdigest()


def _tree_subject(context: ValidatorContext) -> Optional[str]:
    """Digest the repository state a tool-using judge reads.

    HEAD names the commit under judgement; the diff anchor and the working
    diff/status are folded in so an uncommitted change is not silently
    treated as the commit that was judged.  Returns None when the state
    cannot be established — the caller must then not cache the verdict.
    """
    git = getattr(context, "git_mcp", None)
    if git is None:
        return None
    try:
        head = git.get_head_sha()
    except Exception as e:  # noqa: BLE001 — no tree identity, no cache
        logger.debug("Verdict cache: could not read HEAD for tree subject: %s", e)
        return None
    if not isinstance(head, str) or not head.strip():
        return None
    parts = [f"head:{head.strip()}"]
    base = getattr(context, "base_ref", None)
    if base:
        parts.append(f"base:{base}")
    for getter in ("read_diff", "get_status"):
        fn = getattr(git, getter, None)
        if fn is None:
            continue
        try:
            value = fn()
        except Exception as e:  # noqa: BLE001 — partial tree identity is no identity
            logger.debug(
                "Verdict cache: %s failed, tree subject unavailable: %s", getter, e
            )
            return None
        if not isinstance(value, str):
            return None
        parts.append(f"{getter}:{hashlib.sha256(value.encode('utf-8')).hexdigest()}")
    return "tree|" + "|".join(parts)


def _judge_reads_tree(v: Validator, context: ValidatorContext) -> bool:
    """True when this dispatch will run the read-only tool loop."""
    declared = getattr(v, "tools", None) or []
    return bool(
        declared
        and context.workspace_mcp is not None
        and context.git_mcp is not None
        and context.completion_fn is not None
    )


def _tree_subject_for(context: ValidatorContext) -> Optional[str]:
    """Return the tree subject, honouring the once-per-pass precomputation."""
    if getattr(context, "verdict_tree_subject_ready", False):
        return context.verdict_tree_subject
    return _tree_subject(context)


def _verdict_subject_kind(
    v: Validator, context: ValidatorContext, reg: Any
) -> Optional[str]:
    """Classify what this judge's answer depends on: ``"tree"``, ``"spec"``, None.

    The classification is structural, not inherited: a judge that reads the
    tree is tree-keyed, and **every post-execute judge is tree-keyed** because
    a judgement about produced work is a judgement about the work.  Phase is
    authoritative here because an inherited class declaration is exactly the
    thing that fails silently: ``AcceptanceValidator`` inherits ``"spec"``
    from ``LLMValidator``, yet it judges the artifacts the coder just wrote.
    Keying it on the unchanged spec would reuse attempt one's verdict about
    code attempt two rewrote - and because the reused prose is byte-identical,
    recovery-stall detection would read it as a repeated verdict and halt a
    loop a judge was never asked about.
    """
    if _judge_reads_tree(v, context):
        return "tree"

    always_register = {"quality", "protocol"}
    cls = reg.lookup(v.validator_type) if (
        v.criteria or v.validator_type in always_register
    ) else None
    kind = getattr(cls, "cache_subject", None) if cls is not None else None
    if kind is None and cls is None and context.completion_fn is not None and v.criteria:
        # The fallback single-completion LLM path.
        kind = "spec"

    if kind == "tree":
        return "tree"
    if kind == "spec":
        if getattr(context, "phase", "") == "post_execute":
            return "tree"
        return "spec"
    return None


def _verdict_subject(
    v: Validator, context: ValidatorContext, reg: Any
) -> Tuple[Optional[str], bool]:
    """Return ``(subject, cacheable)`` for this judge.

    The subject is what the judge's answer actually depends on (see
    :func:`_verdict_subject_kind`).  A tree subject is precomputed once per
    validate pass (see :func:`run_validators`); the fallback here is for
    direct dispatch calls that bypass the runner.
    """
    kind = _verdict_subject_kind(v, context, reg)
    if kind == "tree":
        subject = _tree_subject_for(context)
        return subject, subject is not None
    if kind == "spec":
        return _spec_subject(context), True
    return None, False


def _result_from_cache(record: Dict[str, Any]) -> ValidatorResult:
    """Rebuild a stored verdict, marked as reused."""
    return ValidatorResult(
        validator_id=record.get("validator_id", ""),
        severity=record.get("severity"),
        justification=record.get("justification", ""),
        cited_criteria=record.get("cited_criteria") or None,
        reused=True,
    )


def dispatch_validator(
    v: Validator, context: ValidatorContext, reg: Any
) -> ValidatorResult:
    """Resolve *v*'s class via the registry and evaluate it.

    The single point where a verdict is produced, and therefore where a
    verdict is cached (#246).  The cached value is what the judge returned,
    before any severity cap the protocol applies afterwards, so a reused
    verdict is capped exactly like a fresh one.

    Never raises — failures become ``error=True`` ValidatorResults (which the
    policy evaluator maps to ``validator_error`` / fail-closed).
    """
    cache = getattr(context, "verdict_cache", None)
    key: Optional[str] = None
    if cache is not None:
        subject, cacheable = _verdict_subject(v, context, reg)
        if cacheable and subject:
            mode = getattr(context, "current_mode", None)
            mode_id = getattr(mode, "mode_id", None) or getattr(context, "mode_name", "")
            protocol = getattr(context, "protocol", None)
            try:
                key = compute_verdict_key(
                    validator_id=v.validator_id,
                    validator_type=v.validator_type,
                    criteria=v.criteria,
                    tools=v.tools,
                    model=context.model,
                    protocol_id=str(getattr(protocol, "protocol_id", "") or ""),
                    protocol_version=str(getattr(protocol, "version", "") or ""),
                    mode_id=str(mode_id or ""),
                    phase=getattr(context, "phase", ""),
                    subject=subject,
                    max_tokens=getattr(context, "max_tokens", None),
                    max_tool_turns=getattr(context, "max_tool_turns", None),
                )
                cached = cache.get(key)
            except Exception as e:  # noqa: BLE001 — cache must never halt a run
                logger.debug("Verdict cache lookup failed (judging fresh): %s", e)
                cached = None
            if cached is not None:
                logger.debug(
                    "Verdict cache hit for validator %s (%s)",
                    v.validator_id, context.phase,
                )
                return _result_from_cache(cached)

    result = _evaluate_validator(v, context, reg)

    if cache is not None and key is not None and _is_cacheable_verdict(result):
        try:
            cache.put(key, result)
        except Exception as e:  # noqa: BLE001 — cache writes must never halt
            logger.debug("Verdict cache store failed (verdict stands): %s", e)
    return result


def _evaluate_validator(
    v: Validator, context: ValidatorContext, reg: Any
) -> ValidatorResult:
    """Evaluate *v* with no cache in the way (the judge itself)."""
    always_register = {"quality", "protocol"}
    cls = reg.lookup(v.validator_type) if (
        v.criteria or v.validator_type in always_register
    ) else None
    if cls is not None:
        try:
            instance = cls(validator_spec=v)
            result = instance.evaluate(context)
        except Exception as e:  # noqa: BLE001 — validator isolation boundary
            # Isolation must not destroy the cause: the halt will say
            # validator_error, and the operator needs the failure's type and
            # message without re-running under a debug switch. The type names
            # the class of fault (an auth rejection is not a syntax error);
            # it goes into the log and into the surfaced justification.
            logger.warning(
                "Validator %s (%s) raised: %s: %s",
                v.validator_id, v.validator_type, type(e).__name__, e,
            )
            result = ValidatorResult(
                validator_id=v.validator_id,
                severity="blocker",
                justification=f"Validator error ({type(e).__name__}): {e}",
                error=True,
            )
        return enrich_result_with_criteria(result, v.criteria)

    if context.completion_fn and v.criteria:
        from snodo.validators.llm_validator import LLMValidator

        try:
            instance = LLMValidator(validator_spec=v)
            result = instance.evaluate(context)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "LLM validation for %s failed: %s: %s",
                v.validator_id, type(e).__name__, e,
            )
            result = ValidatorResult(
                validator_id=v.validator_id,
                severity="blocker",
                justification=f"LLM validation failed ({type(e).__name__}): {e}",
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
    verdict_cb: Any = None,
    artifacts: Optional[List[str]] = None,
    base_ref: Optional[str] = None,
    verdict_cache: Any = None,
) -> Tuple[List[ValidatorResult], Dict[str, str]]:
    """Run a list of validators against a task and return ordered results.

    This is the shared multi-validator runner.  *dispatch_fn* defaults to
    :func:`dispatch_validator`; the engine passes its own ``_dispatch_one``
    method so tests can monkey-patch it (see tests/engine/test_validator_model_override.py).

    Two callbacks carry two different things and never share a signature:
    *progress_cb* receives ongoing-work narration as a single string (a
    validator starting, a tool turn, a validator finishing); *verdict_cb*
    receives a landed verdict as ``(validator_id, ValidatorResult)``.  Each is
    wrapped in a :class:`ProgressSink` before any validator sees it, so a
    broken sink is reported once and can never halt the quorum.

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
    from snodo.engine.progress import ensure_progress_sink
    from snodo.validators.registry import _default_registry as reg

    if dispatch_fn is None:
        dispatch_fn = dispatch_validator

    progress_sink = ensure_progress_sink(progress_cb, "validator progress")
    verdict_sink = ensure_progress_sink(verdict_cb, "validator verdict")

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

    # Wrap completion_fn with task-scoped header injection. Headers cannot
    # be bound at build time (task_id not available yet) but must be resolved
    # at call time. This wrapper makes headers reach every call site by
    # construction — no validator can omit them.
    task_aware_completion_fn = completion_fn
    if completion_fn is not None and task.id:
        task_aware_completion_fn = _wrap_completion_fn_with_headers(completion_fn, task.id)

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
        completion_fn=task_aware_completion_fn,
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
        progress_callback=progress_sink,
        verdict_callback=verdict_sink,
        base_ref=base_ref,
        verdict_cache=verdict_cache,
    )

    # The produced change is read ONCE here, before the validator pool
    # starts, and shared by every post-execute judge through the context
    # (Fixes #267): reconstruction of "what did the coder do" is the
    # engine's job, not the judge's, and it must not depend on whether the
    # project's protocol happened to grant read_diff_between_refs.
    # Pre-execute judges review a proposal and get nothing.
    if phase == "post_execute":
        context.change_context = build_change_context(git_mcp, base_ref)

    # The tree does not move within a validate pass, so digest it once here,
    # before the pool starts: every tree-keyed judge shares the digest, the
    # git read happens once rather than once per validator, and it never runs
    # concurrently under the pool.
    if verdict_cache is not None and any(
        _verdict_subject_kind(v, context, reg) == "tree" for v in validators
    ):
        context.verdict_tree_subject = _tree_subject(context)
        context.verdict_tree_subject_ready = True

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

    def _dispatch_with_progress(v: Validator, ctx: ValidatorContext) -> ValidatorResult:
        # Emitted from inside the worker, not at submit time: with a bounded
        # pool a queued validator has not started yet, and an operator watching
        # "which are still out" must not be told otherwise.
        if progress_sink is not None:
            progress_sink(f"    {v.validator_id}: started")
        return dispatch_fn(v, ctx, reg)

    with ThreadPoolExecutor(max_workers=min(len(validators), 4)) as executor:
        futures = {}
        for v in validators:
            override_model = overrides.get(v.validator_id)
            effective_model = override_model or v.model or default_model or DEFAULT_MODEL
            ctx = copy.copy(context)
            ctx.model = effective_model
            future = executor.submit(_dispatch_with_progress, v, ctx)
            futures[future] = v.validator_id

        for future in as_completed(futures):
            vid = futures[future]
            try:
                result = future.result()
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Validator %s failed outside the dispatch boundary: %s: %s",
                    vid, type(e).__name__, e,
                )
                result = ValidatorResult(
                    validator_id=vid,
                    severity="blocker",
                    justification=f"Validator error ({type(e).__name__}): {e}",
                    error=True,
                )
            if progress_sink is not None:
                progress_sink(f"    {vid}: finished")
            if result is not None:
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
                        severity_original=original_severity,
                        abstention_reason=getattr(result, "abstention_reason", None),
                        reused=getattr(result, "reused", False),
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
                    and result.severity is not None
                ):
                    from snodo.compiler.models import Severity

                    if Severity(result.severity) > v_obj.severity_cap:
                        original_severity = result.severity
                        result = ValidatorResult(
                            validator_id=result.validator_id,
                            severity=v_obj.severity_cap.value,
                            justification=result.justification,
                            cited_criteria=result.cited_criteria,
                            severity_original=original_severity,
                            abstention_reason=getattr(result, "abstention_reason", None),
                            reused=getattr(result, "reused", False),
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
                # Report the *final* (post-cap) severity so the operator's view
                # matches the audit record; carry the pre-cap value alongside it.
                # The sink already swallows and reports its own failure once.
                if verdict_sink is not None:
                    verdict_sink(vid, result)
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
    """Bind *base_fn* to *model*'s routing name and resolved credentials.

    THE single home of the completion-binding rule: the name bound to a
    completion must be litellm's routing name, and its api_base (and api_key)
    must be resolved alongside it from the configured name. Every call site —
    the engine loop, the validator runner, and the MCP server — reaches the
    rule here; there is deliberately no second implementation.

    A provider block named for itself ("ocgo/...") is not a provider litellm
    knows, so binding the raw configured name makes every call through this
    partial fail with "LLM Provider NOT provided" unless the caller happens to
    override it. api_base and api_key are resolved from the CONFIGURED name,
    which is the key the provider block is stored under.

    The api_key is bound directly to avoid credential collision when multiple
    OpenAI-compatible providers exist in one run (#237). Call sites pass the
    configured name through ``_configured_model`` (for task-scoped headers)
    and never a ``model`` kwarg, which would override this binding without its
    api_base.
    """
    import functools

    from snodo.config import ConfigManager

    kwargs: Dict[str, Any] = {"model": ConfigManager.resolve_litellm_model(model)}
    api_base = ConfigManager.resolve_api_base(model)
    if api_base:
        kwargs["api_base"] = api_base

    api_key = ConfigManager().get_key_for_model(model)
    if api_key:
        kwargs["api_key"] = api_key

    return functools.partial(base_fn, **kwargs)


def _wrap_completion_fn_with_headers(completion_fn: Any, task_id: str) -> Any:
    """Wrap a completion function to automatically resolve and attach provider headers.

    Headers are task-scoped (e.g., opencode's x-opencode-session), so they must
    be resolved at call time, not at build time. This wrapper ensures all
    call sites — including validators that declare no tools and
    protocol_adherence — automatically receive headers by construction,
    making it impossible for a new call site to omit them.

    Header resolution uses the configured model name (passed via _configured_model),
    not the model key which may be litellm-resolved. This ensures self-hosted and
    gateway providers with custom snodo config block names resolve correctly.

    Args:
        completion_fn: The underlying completion function (typically a partial)
        task_id: The task's unique identifier for header resolution

    Returns:
        A wrapper function that intercepts calls and injects resolved headers
    """
    from snodo.config import ConfigManager

    def headers_aware_wrapper(**kwargs: Any) -> Any:
        # Use _configured_model (set by validators) for header resolution, not
        # the "model" kwarg which may be litellm-resolved. Call sites that don't
        # set _configured_model fall back to the "model" kwarg (for compatibility).
        resolution_model = kwargs.pop("_configured_model", None) or kwargs.get("model")
        if resolution_model and "extra_headers" not in kwargs:
            # Resolve headers for this specific model and task combination
            extra_headers = ConfigManager.resolve_extra_headers(
                resolution_model, task_id=task_id
            )
            if extra_headers:
                kwargs["extra_headers"] = extra_headers
        return completion_fn(**kwargs)

    return headers_aware_wrapper


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
    """Map (validator results, policy decision) to one of the four validation
    statuses (ADR 015):

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
