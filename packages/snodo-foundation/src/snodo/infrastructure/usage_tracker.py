"""LLM usage + cost tracking via litellm CustomLogger.

FILE: snodo/infrastructure/usage_tracker.py

Captures per-call token usage, cost, timing, and correlation
(job_id/task_id/role) from litellm's log_success_event callback.
Persists records to job state.json keyed by job_id.
"""

import logging
import os
import time
from pathlib import Path

from litellm import CustomLogger

_logger = logging.getLogger(__name__)


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
        try:
            usage = getattr(response_obj, "usage", None)
            prompt_tokens = usage.prompt_tokens if usage else 0
            completion_tokens = usage.completion_tokens if usage else 0
        except Exception:
            prompt_tokens = 0
            completion_tokens = 0

        try:
            import litellm
            cost = litellm.completion_cost(completion_response=response_obj)
        except Exception:
            cost = None

        if cost is None and prompt_tokens + completion_tokens > 0:
            model_name = kwargs.get("model", "") if isinstance(kwargs, dict) else ""
            try:
                from snodo.infrastructure.model_catalog import lookup as catalog_lookup
                meta = catalog_lookup(model_name)
                inp = meta.get("input_cost")
                outp = meta.get("output_cost")
                if isinstance(inp, (int, float)) and isinstance(outp, (int, float)):
                    cost = (prompt_tokens * inp) + (completion_tokens * outp)
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
        model = kwargs.get("model", "unknown") if isinstance(kwargs, dict) else "unknown"

        # Handle duration_ms safely without assuming float or int
        duration_ms = 0.0
        try:
            if hasattr(end_time, "__sub__") and hasattr(start_time, "__sub__"):
                diff = end_time - start_time
                if hasattr(diff, "total_seconds"):
                    duration_ms = round(diff.total_seconds() * 1000, 2)
                elif isinstance(diff, (int, float)):
                    duration_ms = round(float(diff) * 1000, 2)
        except Exception:
            duration_ms = 0.0

        record = {
            "timestamp": time.time(),
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost": cost,
            "duration_ms": duration_ms,
            "job_id": job_id,
            "task_id": task_id,
            "role": role,
        }

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
