"""Task start and completion recording for runs.

FILE: snodo/cli/commands/task_record.py

Moved whole out of ``snodo.cli.commands.run_cmd`` (Fixes #382): the task
record is the run's durable account of what happened — start metadata, final
status, the halt payload, and the measured cost — and its writers form one
concern the command only calls into. The behaviour here is unchanged, line
for line, from where it lived; ``run_cmd`` re-exports both functions so every
existing import path keeps working.
"""

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Optional

from snodo.compiler.models import Protocol

_logger = logging.getLogger(__name__)


def _record_task_start(project_root: str, task_id: str, spec: str) -> None:
    """Record initial task metadata under .snodo/tasks/<task_id>/state.json."""
    if os.environ.get("SNODO_BENCHMARK") == "1":
        return
    try:
        from snodo.infrastructure.state import atomic_update_json

        task_dir = Path(project_root) / ".snodo" / "tasks" / task_id

        def _update(state: dict) -> None:
            state["task_id"] = task_id
            state["description"] = spec
            if "created_at" not in state:
                state["created_at"] = time.time()
            state["started_at"] = time.time()
            state["status"] = "running"
            # A foreground run recorded no pid anywhere, so a monitor could not
            # tell a slow coder from a process that died an hour ago. Record it
            # alongside started_at so liveness is answerable the same way it
            # already is for a background job (jobs/wrapper.py writes its own).
            state["pid"] = os.getpid()

        atomic_update_json(task_dir, "state.json", _update)
    except Exception as e:
        _logger.debug("Could not record task start: %s", e)


def _record_task_completion(
    project_root: str,
    task_id: str,
    status: str,
    halt_payload: Optional[dict] = None,
    protocol: Optional[Protocol] = None,
    model: Optional[str] = None,
) -> None:
    """Record final task completion, halt payload, and measured task cost."""
    if os.environ.get("SNODO_BENCHMARK") == "1":
        return
    try:
        from snodo.infrastructure.state import atomic_update_json
        from snodo.version import __version__

        task_dir = Path(project_root) / ".snodo" / "tasks" / task_id
        job_id = os.environ.get("SNODO_JOB_ID") or ""
        job_dir = Path(project_root) / ".snodo" / "jobs" / job_id if job_id else None

        def _task_cost(state: dict) -> dict:
            usage = state.get("usage")
            usage = usage if isinstance(usage, list) else []
            token_records = [
                record for record in usage
                if isinstance(record, dict)
                and all(isinstance(record.get(key), (int, float)) for key in (
                    "prompt_tokens", "completion_tokens", "total_tokens",
                ))
            ]
            tokens = None
            if token_records:
                tokens = {
                    key: sum(record[key] for record in token_records)
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                }

            report = (halt_payload or {}).get("coder_report") or {}
            turns = report.get("turns_used")
            if not isinstance(turns, (int, float)):
                turns = len(usage) if usage else None

            attempts = (halt_payload or {}).get("attempts", {}).get("total")
            started = state.get("started_at") or state.get("created_at")
            completed = state.get("completed_at")
            duration = None
            if isinstance(started, (int, float)) and isinstance(completed, (int, float)):
                duration = max(0.0, round(completed - started, 3))

            task_spec = (halt_payload or {}).get("task_spec") or state.get("description") or ""
            fixture = hashlib.sha256(task_spec.encode("utf-8")).hexdigest() if task_spec else None
            return {
                "tokens": tokens,
                "turns": turns,
                "attempts": attempts if isinstance(attempts, (int, float)) else None,
                "duration_seconds": duration,
                "change_size": (halt_payload or {}).get("change_size"),
                "provenance": {
                    "snodo_version": __version__,
                    "protocol_id": getattr(protocol, "protocol_id", None),
                    "protocol_version": getattr(protocol, "version", None),
                    "fixture": fixture,
                    "model": (halt_payload or {}).get("coder_model") or model,
                    "coder": (halt_payload or {}).get("coder"),
                },
            }

        def _update(state: dict) -> None:
            state["task_id"] = task_id
            state["completed_at"] = time.time()
            state["status"] = status
            if halt_payload:
                state["halt"] = halt_payload
                findings = halt_payload.get("findings")
                if findings is not None:
                    state["findings"] = findings
            state["cost"] = _task_cost(state)

        atomic_update_json(task_dir, "state.json", _update)
        if job_dir and job_dir.is_dir():
            atomic_update_json(job_dir, "state.json", lambda state: state.update({"cost": _task_cost(state)}))
    except Exception as e:
        _logger.debug("Could not record task completion: %s", e)
