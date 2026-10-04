"""Best-effort plan ownership details for cloud liveness job rows."""

from __future__ import annotations

import json
from pathlib import Path


def plan_job_progress(job_dir: Path, plans_dir: Path) -> dict[str, str]:
    """Return plan and active task identity for a plan/queue job, if readable.

    This is deliberately a small, read-only probe. Corrupt, missing, or
    inaccessible state is simply omitted from the liveness payload.
    """
    try:
        task = json.loads((job_dir / "task.json").read_text(encoding="utf-8"))
        plan = task.get("plan_name") if isinstance(task, dict) else None
        if not isinstance(plan, str) or not plan:
            return {}
        status = json.loads((plans_dir / plan / "status.json").read_text(encoding="utf-8"))
        entries = status.get("tasks", {}) if isinstance(status, dict) else {}
        active = sorted(
            str(task_id) for task_id, entry in entries.items()
            if (entry.get("status") if isinstance(entry, dict) else entry) in {"running", "in_progress"}
        )
        result = {"plan": plan}
        if active:
            result["task_ref"] = active[0]
        return result
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError, TypeError, ValueError):
        return {}
