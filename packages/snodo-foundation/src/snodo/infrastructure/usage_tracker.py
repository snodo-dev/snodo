"""LLM usage + cost tracking via litellm CustomLogger.

FILE: snodo/infrastructure/usage_tracker.py

Captures per-call token usage, cost, timing, and correlation
(job_id/task_id/role) from litellm's log_success_event callback.
Persists records to job state.json keyed by job_id.
"""

import json
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

        record = {
            "timestamp": time.time(),
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost": cost,
            "duration_ms": (end_time - start_time) * 1000,
            "job_id": job_id,
            "task_id": task_id,
            "role": role,
        }

        self._calls.append(record)

        if job_id != "unknown" and job_id.startswith("j_"):
            try:
                _persist_usage(job_id, record)
            except Exception as e:
                _logger.warning("Failed to persist usage for job %s: %s", job_id, e)
        if task_id != "unknown" and task_id.startswith("task_"):
            try:
                _persist_task_usage(task_id, record)
            except Exception as e:
                _logger.warning("Failed to persist usage for task %s: %s", task_id, e)


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
    tasks_dir = Path(project_root) / ".snodo" / "tasks"
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    _append_usage(task_dir, record, task_id=task_id)


def _append_usage(target_dir: Path, record: dict, task_id: str = "") -> None:
    state_path = target_dir / "state.json"
    state = {}
    if state_path.exists():
        try:
            with open(state_path) as f:
                state = json.load(f)
        except Exception as e:
            _logger.warning("Failed to read state for usage tracking: %s", e)
    if not isinstance(state, dict):
        state = {}
    usage_list = state.get("usage", [])
    if not isinstance(usage_list, list):
        usage_list = []
    usage_list.append(record)
    state["usage"] = usage_list
    if task_id and "task_id" not in state:
        state["task_id"] = task_id
    tmp = target_dir / "state.json.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(str(tmp), str(state_path))


def _find_project_root(job_id: str = "") -> str | None:
    """Resolve project root.

    Priority:
      1. ``SNODO_PROJECT_ROOT`` env var (set by wrapper.py for bg jobs)
      2. Walk up from cwd via resolve_project_root()
    """
    env_root = os.environ.get("SNODO_PROJECT_ROOT")
    if env_root:
        return env_root
    try:
        from snodo.paths import resolve_project_root
        return resolve_project_root()
    except Exception:
        d = Path.cwd()
        for parent in [d] + list(d.parents):
            if (parent / ".snodo").is_dir():
                return str(parent)
        return None
