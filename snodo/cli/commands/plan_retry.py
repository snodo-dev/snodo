"""Retry lineage helpers for plan task execution."""

from pathlib import Path


def current_plan_spec(project_root: str, plan_name: str, task_id: str) -> str:
    """Read a plan task's current spec, returning empty when it is absent."""
    wave_id = task_id.split(".", 1)[0]
    path = (
        Path(project_root) / ".snodo" / "plans" / plan_name
        / f"wave_{wave_id}" / f"{task_id}_task.md"
    )
    return path.read_text() if path.is_file() else ""


def spec_was_replaced(planner, plan_name: str, task_id: str, failure: dict) -> bool:
    """Return whether the current plan spec differs from the failed input."""
    current = current_plan_spec(
        str(planner.project_root), plan_name, task_id
    )
    if not current:
        return False
    from snodo.core.spec import spec_text

    recorded = spec_text(failure.get("original_spec") or failure.get("spec", ""))
    return spec_text(current) != recorded
