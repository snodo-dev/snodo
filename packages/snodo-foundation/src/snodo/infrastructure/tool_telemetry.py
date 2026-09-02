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

import json
import logging
import os
from pathlib import Path

_logger = logging.getLogger(__name__)


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
        return

    job_id = target_id if target_id and target_id.startswith("j_") else None
    task_id = None
    if target_id and target_id.startswith("task_"):
        task_id = target_id
    elif record.get("task_ref") and record.get("task_ref") != "unknown" and str(record.get("task_ref")).startswith("task_"):
        task_id = str(record["task_ref"])

    if not job_id and not task_id:
        return

    project_root = _find_project_root()
    if not project_root:
        return

    from snodo.project import _is_system_root_or_temp
    if _is_system_root_or_temp(project_root):
        return

    if job_id:
        _append_telemetry(Path(project_root) / ".snodo" / "jobs" / job_id, record)
    if task_id:
        _append_telemetry(Path(project_root) / ".snodo" / "tasks" / task_id, record)


def _append_telemetry(target_dir: Path, record: dict) -> None:
    """Safely append a telemetry record to target_dir/state.json."""
    try:
        from snodo.project import _is_system_root_or_temp

        if _is_system_root_or_temp(target_dir.parent.parent):
            return

        target_dir.mkdir(parents=True, exist_ok=True)
        state_path = target_dir / "state.json"
        state = {}
        if state_path.exists():
            try:
                with open(state_path) as f:
                    state = json.load(f)
            except Exception as e:
                _logger.warning("Failed to read state for tool telemetry: %s", e)
        if not isinstance(state, dict):
            state = {}
        telemetry = state.get("tool_telemetry", [])
        if not isinstance(telemetry, list):
            telemetry = []
        telemetry.append(record)
        state["tool_telemetry"] = telemetry
        if "task_id" not in state and record.get("task_ref") and str(record["task_ref"]).startswith("task_"):
            state["task_id"] = str(record["task_ref"])
        tmp = target_dir / "state.json.tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(str(tmp), str(state_path))
    except Exception as e:
        _logger.warning("Failed to append tool telemetry to %s: %s", target_dir, e)


def _find_project_root(job_id: str = "", task_id: str = "") -> str | None:
    """Resolve project root.

    Priority:
      1. ``SNODO_PROJECT_ROOT`` env var (set by wrapper.py for bg jobs)
      2. Walk up from cwd via resolve_project_root()
    """
    from snodo.project import _is_system_root_or_temp

    env_root = os.environ.get("SNODO_PROJECT_ROOT")
    if env_root:
        if _is_system_root_or_temp(env_root):
            return None
        return env_root
    try:
        from snodo.paths import resolve_project_root
        root = resolve_project_root()
        if root and not _is_system_root_or_temp(root):
            return root
        return None
    except Exception:
        return None
