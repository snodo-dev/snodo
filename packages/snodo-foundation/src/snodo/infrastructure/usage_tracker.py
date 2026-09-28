"""LLM usage + cost tracking via litellm CustomLogger.

FILE: snodo/infrastructure/usage_tracker.py

Captures per-call token usage, cost, timing, and correlation
(job_id/task_id/role) from litellm's log_success_event callback, plus the
model the provider reported serving beside the one requested
(see snodo.infrastructure.model_provenance).
Persists records to job state.json keyed by job_id.
"""

import logging
import os
import time
from pathlib import Path
from typing import Optional

from litellm import CustomLogger

_logger = logging.getLogger(__name__)


def usage_tokens_of(response, kind: str) -> Optional[int]:
    """Extract prompt/completion token counts from a litellm response.

    Returns None when the response carries no usage or a count is absent.
    """
    try:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        if kind == "prompt":
            value = getattr(usage, "prompt_tokens", None)
        else:
            value = getattr(usage, "completion_tokens", None)
        return int(value) if value is not None else None
    except Exception:
        return None


class UsageTracker(CustomLogger):
    """litellm CustomLogger — captures usage, cost, timing per completion.

    Instantiated once at module level in coders/litellm.py:28.
    litellm calls log_success_event on every completion() return.
    """

    def __init__(self):
        super().__init__()
        self._calls: list[dict] = []

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        """litellm callback — capture one completion record."""
        self._record(kwargs, response_obj, start_time, end_time, outcome="success")

    def log_failure_event(self, kwargs, exception, start_time, end_time):
        """litellm callback — capture failed completion attempts too."""
        self._record(kwargs, None, start_time, end_time, outcome="error",
                     error_class=type(exception).__name__)

    def _record(self, kwargs, response_obj, start_time, end_time, outcome, error_class=None):
        prompt_tokens = usage_tokens_of(response_obj, "prompt")
        completion_tokens = usage_tokens_of(response_obj, "completion")
        usage = getattr(response_obj, "usage", None) if response_obj is not None else None
        cache_read_tokens = _optional_int(usage, "cache_read_input_tokens", "cached_tokens", "cache_read_tokens")
        cache_write_tokens = _optional_int(usage, "cache_creation_input_tokens", "cache_write_tokens")
        # LiteLLM's OpenAI-compatible providers may put cache counters in
        # prompt_tokens_details rather than directly on Usage.
        details = getattr(usage, "prompt_tokens_details", None) if usage is not None else None
        if cache_read_tokens is None:
            cache_read_tokens = _optional_int(details, "cached_tokens", "cache_read_tokens")
        if cache_write_tokens is None:
            cache_write_tokens = _optional_int(details, "cache_write_tokens", "cache_creation_input_tokens")

        cost = _provider_cost_of(response_obj, kwargs)

        cost_source = "provider" if cost is not None else None
        if cost is None and prompt_tokens is not None and completion_tokens is not None:
            model_name = kwargs.get("model", "") if isinstance(kwargs, dict) else ""
            try:
                from snodo.infrastructure.model_catalog import lookup as catalog_lookup
                meta = catalog_lookup(model_name)
                inp = meta.get("input_cost")
                outp = meta.get("output_cost")
                if isinstance(inp, (int, float)) and isinstance(outp, (int, float)):
                    # models.dev publishes dollars per million tokens; the
                    # litellm fallback publishes dollars per token.  The unit
                    # is carried on the lookup result so the per-token cost is
                    # never scaled by the wrong factor.
                    if meta.get("cost_unit", "per_1m") == "per_1m":
                        inp = inp / 1_000_000
                        outp = outp / 1_000_000
                    cost = (prompt_tokens * inp) + (completion_tokens * outp)
                    cost_source = "estimate"
            except Exception as e:
                _logger.debug("Catalog cost calculation failed: %s", e)

        meta: dict = {}
        if isinstance(kwargs, dict):
            meta_top = kwargs.get("metadata", {}) or {}
            meta_params = kwargs.get("litellm_params", {}).get("metadata", {}) or {}
            meta = {**meta_top, **meta_params}

        # SNODO_JOB_ID is the authoritative source for background jobs
        # (set by wrapper.py before calling cli_main). Metadata is the
        # fallback for inline runs (where no env var exists).
        job_id = os.environ.get("SNODO_JOB_ID") or meta.get("job_id", "unknown")
        task_id = meta.get("task_id", "unknown")
        role = meta.get("role", "unknown")
        model = kwargs.get("model") if isinstance(kwargs, dict) else None
        model = model or None
        llm_params = kwargs.get("litellm_params", {}) if isinstance(kwargs, dict) else {}
        provider = (kwargs.get("custom_llm_provider") or llm_params.get("custom_llm_provider")) if isinstance(llm_params, dict) else None
        provider = provider or (kwargs.get("custom_llm_provider") if isinstance(kwargs, dict) else None)
        # Provenance, not configuration: the provider's own name for what it
        # served, recorded beside the requested one. None means the provider
        # reported nothing usable — absence, never an assumed match.
        from snodo.infrastructure.model_provenance import served_model_of
        served_model = served_model_of(response_obj)

        # Handle duration_ms safely without assuming float or int
        duration_ms = None
        try:
            if hasattr(end_time, "__sub__") and hasattr(start_time, "__sub__"):
                diff = end_time - start_time
                if hasattr(diff, "total_seconds"):
                    duration_ms = round(diff.total_seconds() * 1000, 2)
                elif isinstance(diff, (int, float)):
                    duration_ms = round(float(diff) * 1000, 2)
        except Exception:
            duration_ms = None

        total_tokens = (prompt_tokens + completion_tokens
                        if prompt_tokens is not None and completion_tokens is not None else None)

        record = {
            "timestamp": time.time(),
            "model": model,
            "served_model": served_model,
            "provider": provider,
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost": cost,
            "cost_source": cost_source,
            "duration_ms": duration_ms,
            "outcome": outcome,
            "job_id": job_id,
            "task_id": task_id,
            "role": role,
        }
        if error_class:
            record["error_class"] = error_class

        self._calls.append(record)

        if job_id != "unknown" and job_id.startswith("j_"):
            try:
                _persist_usage(job_id, record)
            except Exception as e:
                _logger.debug("Failed to persist usage for job %s: %s", job_id, e)
        if task_id != "unknown" and task_id.startswith("task_"):
            try:
                _persist_task_usage(task_id, record)
            except Exception as e:
                _logger.debug("Failed to persist usage for task %s: %s", task_id, e)


def _optional_int(obj, *names):
    if obj is None:
        return None
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _provider_cost_of(response, kwargs):
    """Read only a cost explicitly attached to this provider response."""
    hidden = getattr(response, "_hidden_params", None) if response is not None else None
    if isinstance(hidden, dict):
        value = hidden.get("response_cost")
    else:
        value = getattr(hidden, "response_cost", None) if hidden is not None else None
    if value is None and isinstance(kwargs, dict):
        params = kwargs.get("litellm_params", {})
        if isinstance(params, dict):
            value = params.get("response_cost")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _persist_usage(job_id: str, record: dict) -> None:
    """Append a usage record to the job's state.json usage list."""
    project_root = _find_project_root()
    if not project_root:
        return
    jobs_dir = Path(project_root) / ".snodo" / "jobs"
    job_dir = jobs_dir / job_id
    if not job_dir.is_dir():
        return
    _append_usage(job_dir, record)


def _persist_task_usage(task_id: str, record: dict) -> None:
    """Append a usage record to the task's state.json usage list."""
    project_root = _find_project_root()
    if not project_root:
        return
    from snodo.project import _is_system_root_or_temp
    if _is_system_root_or_temp(project_root):
        return
    tasks_dir = Path(project_root) / ".snodo" / "tasks"
    task_dir = tasks_dir / task_id
    _append_usage(task_dir, record, task_id=task_id)


def _append_usage(target_dir: Path, record: dict, task_id: str = "") -> None:
    """Safely append a usage record to target_dir/state.json."""
    try:
        from snodo.infrastructure.state import atomic_update_json

        def _update(state: dict) -> None:
            usage_list = state.get("usage", [])
            if not isinstance(usage_list, list):
                usage_list = []
            usage_list.append(record)
            state["usage"] = usage_list
            if task_id and "task_id" not in state:
                state["task_id"] = task_id

        atomic_update_json(target_dir, "state.json", _update)
    except Exception as e:
        _logger.debug("Failed to append usage tracking to %s: %s", target_dir, e)


def _find_project_root(job_id: str = "") -> str | None:
    """Resolve project root.

    Only uses explicit SNODO_PROJECT_ROOT environment variable (set for jobs and task runs).
    Usage tracking must NEVER walk up the filesystem to guess a root.
    """
    env_root = os.environ.get("SNODO_PROJECT_ROOT")
    if env_root:
        from snodo.project import _is_system_root_or_temp
        if not _is_system_root_or_temp(env_root):
            return env_root
    return None


def record_inplace_coder_run(
    coder: str,
    model: str,
    duration_ms: float,
    job_id: str = "",
    task_id: str = "",
    timed_out: bool = False,
    timeout_seconds: Optional[int] = None,
    outcome: str = "success",
    error_class: Optional[str] = None,
    usage: Optional[dict] = None,
) -> None:
    """Record a completed in-place coder execution in job/task state.json.

    In-place coders (agy, opencode-cli, opencode container) make no litellm
    calls, so the UsageTracker callback never fires for them.  Without this
    function a completed run is indistinguishable in state.json from a run
    that genuinely cost nothing.

    What is measured (and labelled as such):
    - ``duration_ms``: wall-clock duration of the subprocess / HTTP session
    - ``model``: the model string the CLI was asked to use (may be ``""`` when
      the operator did not specify one — the tool then uses its own default)

    Usage supplied by a CLI adapter is recorded only for fields that adapter
    directly observed; all other usage remains null. OpenCode CLI reports its
    provider usage in JSON step-finish events. ``prompt_tokens`` and
    ``completion_tokens`` remain null because the in-place record uses the
    input/output token fields instead.

    The ``"source": "inplace_coder"`` field distinguishes this record from a
    litellm-originated one.  The ``"measured"`` list declares which fields were
    directly observed (as opposed to estimated or derived) so consumers can
    filter with confidence.  References issue #69.

    This function never raises; attribution must not crash the engine loop.
    """
    usage = usage or {}
    record: dict = {
        "timestamp": time.time(),
        "source": "inplace_coder",
        "provider": None,
        "coder": coder,
        "role": "coder",
        "model": model,
        "duration_ms": duration_ms,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_tokens": usage.get("reasoning_tokens"),
        "cache_read_tokens": usage.get("cache_read_tokens"),
        "cache_write_tokens": usage.get("cache_write_tokens"),
        "cost_source": "provider" if usage.get("cost") is not None else None,
        "outcome": outcome,
        # Compatibility fields not exposed in the in-place usage contract.
        "cost": usage.get("cost"),
        "served_model": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        # Explicit declaration: only what is in this list was directly observed.
        "measured": (
            ["duration_ms"]
            + (["model"] if model else [])
            + [
                key
                for key in (
                    "input_tokens", "output_tokens", "reasoning_tokens",
                    "cache_read_tokens", "cache_write_tokens", "cost",
                )
                if usage.get(key) is not None
            ]
            + (["timed_out"] if timed_out else [])
        ),
    }
    if error_class:
        record["error_class"] = error_class
    if timed_out:
        record["timed_out"] = True
        if timeout_seconds is not None:
            record["timeout_seconds"] = timeout_seconds

    # Resolve job / task ids: prefer explicit arguments, fall back to job env var.
    resolved_job_id = job_id or os.environ.get("SNODO_JOB_ID") or ""
    resolved_task_id = task_id or ""

    if resolved_job_id.startswith("j_"):
        record["job_id"] = resolved_job_id
        try:
            _persist_usage(resolved_job_id, record)
        except Exception as e:
            _logger.debug(
                "record_inplace_coder_run: failed to persist for job %s: %s",
                resolved_job_id, e,
            )

    if resolved_task_id.startswith("task_"):
        record["task_id"] = resolved_task_id
        try:
            _persist_task_usage(resolved_task_id, record)
        except Exception as e:
            _logger.debug(
                "record_inplace_coder_run: failed to persist for task %s: %s",
                resolved_task_id, e,
            )
