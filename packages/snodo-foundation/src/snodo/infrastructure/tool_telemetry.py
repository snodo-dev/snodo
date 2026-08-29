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


def persist_tool_telemetry(job_id: str, record: dict) -> None:
    """Append one per-turn telemetry record to the job's state.json.

    No-op when *job_id* is empty/unknown (inline runs without a job) or the
    job directory cannot be resolved — telemetry must never crash the loop.
    """
    if not job_id or job_id == "unknown":
        return
    project_root = _find_project_root(job_id)
    if not project_root:
        return
    job_dir = Path(project_root) / ".snodo" / "jobs" / job_id
    if not job_dir.is_dir():
        return
    state_path = job_dir / "state.json"
    state = {}
    if state_path.exists():
        try:
            with open(state_path) as f:
                state = json.load(f)
        except Exception as e:
            _logger.warning("Failed to read job state for tool telemetry: %s", e)
    telemetry = state.get("tool_telemetry", [])
    if not isinstance(telemetry, list):
        telemetry = []
    telemetry.append(record)
    state["tool_telemetry"] = telemetry
    tmp = job_dir / "state.json.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(str(tmp), str(state_path))


def _find_project_root(job_id: str) -> str | None:
    """Resolve project root for *job_id*.

    Priority:
      1. ``SNODO_PROJECT_ROOT`` env var (set by wrapper.py for bg jobs)
      2. Walk up from cwd (fallback for inline runs)
    """
    env_root = os.environ.get("SNODO_PROJECT_ROOT")
    if env_root:
        candidate = Path(env_root) / ".snodo" / "jobs" / job_id
        if candidate.is_dir():
            return env_root
    d = Path.cwd()
    for parent in [d] + list(d.parents):
        job_dir = parent / ".snodo" / "jobs" / job_id
        if job_dir.is_dir():
            return str(parent)
    return None
