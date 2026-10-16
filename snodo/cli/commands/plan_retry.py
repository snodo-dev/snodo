"""Retry lineage helpers for plan task execution."""

import hashlib
import json
from pathlib import Path


def _normalize_spec(spec: str) -> str:
    """Normalize a spec string for hashing — planner's canonical form."""
    return spec.strip().replace("\r\n", "\n")


def spec_hash_of(spec: str) -> str:
    """Hash used in status.json to identify a spec version."""
    return hashlib.sha256(_normalize_spec(spec).encode()).hexdigest()[:16]


def current_plan_spec(project_root: str, plan_name: str, task_id: str) -> str:
    """Read a plan task's current spec, returning empty when it is absent."""
    wave_id = task_id.split(".", 1)[0]
    path = (
        Path(project_root) / ".snodo" / "plans" / plan_name
        / f"wave_{wave_id}" / f"{task_id}_task.md"
    )
    return path.read_text() if path.is_file() else ""


def _recorded_spec_hash(failure: dict) -> str | None:
    """Hash of the spec recorded at failure, or None when absent."""
    recorded = failure.get("original_spec") or failure.get("spec") or ""
    if not recorded or not recorded.strip():
        return None
    return spec_hash_of(recorded)


def _current_spec_hash(project_root: str, plan_name: str, task_id: str) -> str | None:
    """Hash stored in status.json for the current plan spec, or None."""
    status_path = Path(project_root) / ".snodo" / "plans" / plan_name / "status.json"
    if not status_path.is_file():
        return None
    try:
        data = json.loads(status_path.read_text())
    except Exception:
        return None
    tasks = data.get("tasks", {}) if isinstance(data, dict) else {}
    entry = tasks.get(task_id)
    if isinstance(entry, dict):
        h = entry.get("spec_hash")
        if isinstance(h, str) and h:
            return h
    return None


def spec_was_replaced(planner, plan_name: str, task_id: str, failure: dict) -> bool:
    """Return whether the current plan spec was replaced via the supported path.

    A deliberate replacement through ``generate_spec(..., replace=True)`` updates
    ``status.json: spec_hash``; a hand-edit of the file on disk does not. The
    reset is therefore keyed on the recorded hash, not on file content, honoring
    the #832 contract that hand-edits do not reset the retry budget.
    """
    recorded = _recorded_spec_hash(failure)
    if recorded is None:
        return False
    current = _current_spec_hash(str(planner.project_root), plan_name, task_id)
    if current is None:
        return False
    return current != recorded


def resolve_retry_lineage(project_root: str, plan_name: str, task_id: str, failure: dict) -> tuple[str, bool]:
    """Resolve the effective spec and whether the attempt lineage resets.

    One hash comparison drives both decisions: the spec to judge and whether
    the retry budget restarts. Returns ``(effective_spec, should_reset)`` where
    ``effective_spec`` is the current on-disk spec when a replacement is
    detected, otherwise the recorded spec. ``should_reset`` is True only for a
    deliberate replacement (hash mismatch via status.json).
    """
    recorded_spec = failure.get("original_spec") or failure.get("spec") or ""
    if not plan_name:
        return recorded_spec, False
    recorded_hash = _recorded_spec_hash(failure)
    if recorded_hash is None:
        return recorded_spec, False
    current_hash = _current_spec_hash(project_root, plan_name, task_id)
    if current_hash is None or current_hash == recorded_hash:
        return recorded_spec, False
    disk_spec = current_plan_spec(project_root, plan_name, task_id)
    if not disk_spec:
        return recorded_spec, False
    return disk_spec, True
