"""Per-turn tool-loop telemetry sink.

FILE: snodo/infrastructure/tool_telemetry.py

Emits one structured record per tool-loop turn (coder and validator) to the
job's ``state.json`` under the ``tool_telemetry`` key. This is operational
telemetry, NOT part of the hash-chained audit log (ADR 034): ~50 records per
task do not belong in the attestation chain. Mirrors ``UsageTracker``'s
``_persist_usage`` pattern.

The opencode adapters own their own agent loop and cannot emit these records;
their runs have no per-turn telemetry — an acknowledged consequence of ADR
034's experimental status.
"""

import logging
import os
from pathlib import Path

_logger = logging.getLogger(__name__)

_MAX_DROPPED_RECORDS = 100
_dropped_count: int = 0
_dropped_records: list[dict] = []


def get_dropped_telemetry_count() -> int:
    """Return the total count of tool telemetry records dropped in this process."""
    return _dropped_count


def get_dropped_telemetry_records() -> list[dict]:
    """Return copies of dropped telemetry records metadata in this process."""
    return list(_dropped_records)


def reset_dropped_telemetry() -> None:
    """Reset the dropped telemetry tracking counters and records."""
    global _dropped_count
    _dropped_count = 0
    _dropped_records.clear()


def _record_drop(reason: str, target_id: str, record: dict | None, detail: str = "") -> None:
    """Record a telemetry drop event for diagnostics without interrupting execution."""
    global _dropped_count
    _dropped_count += 1
    if len(_dropped_records) < _MAX_DROPPED_RECORDS:
        _dropped_records.append({
            "reason": reason,
            "target_id": target_id,
            "record": record,
            "detail": detail,
        })
    _logger.debug(
        "Dropped tool telemetry record (%s): target_id=%r, detail=%s",
        reason,
        target_id,
        detail,
    )


def canonical_target_path(path: str) -> str:
    """Return a workspace-relative, canonical form of *path*.

    Strips a leading ``./``, normalises separators to ``/``, and collapses
    ``.``/``..`` segments so the miss-rate metric is not noise from
    ``./src/a.py`` vs ``src/a.py``. Empty/None input stays empty.
    """
    if not path:
        return ""
    p = str(path).strip()
    if not p:
        return ""
    try:
        norm = p.replace("\\", "/")
        norm = Path(norm).as_posix()
        if norm.startswith("./"):
            norm = norm[2:]
        return norm
    except Exception:
        return p


def persist_tool_telemetry(target_id: str, record: dict) -> None:
    """Append one per-turn telemetry record to the job or task's state.json.

    No-op when *target_id* is empty/unknown and no *task_ref* is present, or the
    project root cannot be resolved — telemetry must never crash the loop.
    """
    if not record or not isinstance(record, dict):
        _record_drop("invalid_record", target_id, record, "Record is empty or not a dict")
        return

    job_id = target_id if target_id and target_id.startswith("j_") else None
    task_id = None
    if target_id and target_id.startswith("task_"):
        task_id = target_id
    elif record.get("task_ref") and record.get("task_ref") != "unknown" and str(record.get("task_ref")).startswith("task_"):
        task_id = str(record["task_ref"])

    if not job_id and not task_id:
        _record_drop("no_target_id", target_id, record, "No resolvable job_id or task_id")
        return

    project_root = _find_project_root()
    if not project_root:
        _record_drop("no_project_root", target_id, record, "Project root could not be resolved")
        return

    from snodo.project import _is_system_root_or_temp
    if _is_system_root_or_temp(project_root):
        _record_drop("system_root_or_temp", target_id, record, f"Project root {project_root} is system or temp")
        return

    if job_id:
        _append_telemetry(Path(project_root) / ".snodo" / "jobs" / job_id, record)
    if task_id:
        _append_telemetry(Path(project_root) / ".snodo" / "tasks" / task_id, record)


def _append_telemetry(target_dir: Path, record: dict) -> None:
    """Safely append a telemetry record to target_dir/state.json."""
    try:
        from snodo.project import _is_system_root_or_temp
        from snodo.infrastructure.state import atomic_update_json

        if _is_system_root_or_temp(target_dir.parent.parent):
            _record_drop("target_in_system_or_temp", str(target_dir), record, "Target directory parent is system or temp")
            return

        def _update(state: dict) -> None:
            telemetry = state.get("tool_telemetry", [])
            if not isinstance(telemetry, list):
                telemetry = []
            telemetry.append(record)
            state["tool_telemetry"] = telemetry
            if "task_id" not in state and record.get("task_ref") and str(record["task_ref"]).startswith("task_"):
                state["task_id"] = str(record["task_ref"])

        atomic_update_json(target_dir, "state.json", _update)
    except Exception as e:
        _logger.debug("Failed to append tool telemetry to %s: %s", target_dir, e)
        _record_drop("append_exception", str(target_dir), record, str(e))


def _find_project_root(job_id: str = "", task_id: str = "") -> str | None:
    """Resolve project root.

    Only uses explicit SNODO_PROJECT_ROOT environment variable (set for jobs and task runs).
    Telemetry must NEVER walk up the filesystem to guess a root.
    """
    env_root = os.environ.get("SNODO_PROJECT_ROOT")
    if env_root:
        from snodo.project import _is_system_root_or_temp
        if not _is_system_root_or_temp(env_root):
            return env_root
    return None
