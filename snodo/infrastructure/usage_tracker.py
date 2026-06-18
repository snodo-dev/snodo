"""LLM usage + cost tracking via litellm CustomLogger.

FILE: snodo/infrastructure/usage_tracker.py

Captures per-call token usage, cost, timing, and correlation
(job_id/task_id/role) from litellm's log_success_event callback.
Persists records to job state.json keyed by job_id.
"""

import json
import logging
import time
from pathlib import Path

_logger = logging.getLogger(__name__)


class UsageTracker:
    """litellm CustomLogger — captures usage, cost, timing per completion.

    Instantiated once at module level in coders/litellm.py:28.
    litellm calls log_success_event on every completion() return.
    """

    def __init__(self):
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
            except Exception:
                pass

        meta = (
            kwargs.get("litellm_params", {}).get("metadata", {})
            if isinstance(kwargs, dict) else {}
        )

        job_id = meta.get("job_id", "unknown")
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

        if job_id != "unknown":
            try:
                _persist_usage(job_id, record)
            except Exception:
                pass


def _persist_usage(job_id: str, record: dict) -> None:
    """Append a usage record to the job's state.json usage list."""
    project_root = _find_project_root(job_id)
    if not project_root:
        return
    jobs_dir = Path(project_root) / ".snodo" / "jobs"
    job_dir = jobs_dir / job_id
    if not job_dir.is_dir():
        return
    state_path = job_dir / "state.json"
    state = {}
    if state_path.exists():
        try:
            with open(state_path) as f:
                state = json.load(f)
        except Exception:
            pass
    usage_list = state.get("usage", [])
    if not isinstance(usage_list, list):
        usage_list = []
    usage_list.append(record)
    state["usage"] = usage_list
    tmp = job_dir / "state.json.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    import os
    os.replace(str(tmp), str(state_path))


def _find_project_root(job_id: str) -> str | None:
    """Walk up from cwd to find .snodo/ containing job_id."""
    from pathlib import Path as _Path
    d = _Path.cwd()
    for parent in [d] + list(d.parents):
        job_dir = parent / ".snodo" / "jobs" / job_id
        if job_dir.is_dir():
            return str(parent)
    return None
