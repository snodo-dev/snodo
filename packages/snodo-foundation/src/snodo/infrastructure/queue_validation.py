"""Read-only queue verification and ordering report (ADR 053)."""

from __future__ import annotations

import fcntl
import json
from pathlib import Path
from typing import Any

import yaml


def build_validation_report(project_root: Path, selected: str | None = None) -> dict[str, Any]:
    """Build a report without initializing or pruning the on-disk queue record."""
    project_root = Path(project_root).resolve()
    queue_record = project_root / ".snodo" / "queues.json"
    try:
        data = json.loads(queue_record.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Queue record not found: {queue_record}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid queue record {queue_record}: {exc}") from exc
    queues = data.get("queues") if isinstance(data, dict) else None
    if not isinstance(queues, dict) or any(
        not isinstance(name, str) or not isinstance(plans, list)
        for name, plans in queues.items()
    ):
        raise ValueError(f"Invalid queue record: {queue_record}")
    if selected is not None and selected not in queues:
        raise ValueError(f"Queue does not exist: {selected}")

    # Preserve on-disk order and include all queues when looking for cross-queue
    # dependencies, even if the caller requested one queue's report.
    plan_facts: dict[str, dict[str, Any]] = {}
    for queue_name, plan_names in queues.items():
        for plan_name in plan_names:
            if isinstance(plan_name, str):
                plan_facts[plan_name] = _inspect_plan(project_root, plan_name)

    report_queues = {}
    for queue_name, plans in queues.items():
        if selected is not None and queue_name != selected:
            continue
        entries = [
            {"name": name, "position": index, **plan_facts[name]}
            for index, name in enumerate(plans)
            if isinstance(name, str)
        ]
        report_queues[queue_name] = {
            "runnable": _front_is_runnable(entries[0]) if entries else False,
            "front": entries[0] if entries else None,
            "plans": entries,
            "order_problems": _order_problems(queue_name, entries, queues, plan_facts),
            "runner_active": _lock_is_held(
                project_root / ".snodo" / "queue-locks" / f"{queue_name}.lock"
            ),
        }

    return {
        "ok": True,
        "queues": report_queues,
        "cross_queue_warnings": _cross_queue_warnings(queues, plan_facts),
    }


def _inspect_plan(project_root: Path, name: str) -> dict[str, Any]:
    from snodo.compiler.verifier import verify_plan_dir
    from snodo.infrastructure.worktree import planned_spec_paths, _spec_referenced_paths

    plan_dir = project_root / ".snodo" / "plans" / name
    result = verify_plan_dir(plan_dir, workspace_root=project_root)
    plan_data: dict[str, Any] = {}
    try:
        plan_data = yaml.safe_load((plan_dir / "plan.yml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError, TypeError):
        pass

    cited: set[str] = set()
    creates: set[str] = set()
    for wave in plan_data.get("waves", []) if isinstance(plan_data, dict) else []:
        if not isinstance(wave, dict):
            continue
        wave_id = wave.get("id")
        for task_id in wave.get("tasks", []):
            spec_path = plan_dir / f"wave_{wave_id}" / f"{task_id}_task.md"
            try:
                spec = spec_path.read_text(encoding="utf-8")
            except OSError:
                continue
            cited.update(_spec_referenced_paths(spec))
            creates.update(planned_spec_paths(spec))

    statuses, stopped = _task_statuses(plan_dir, plan_data)
    return {
        "verified": bool(result.passed),
        "verification_errors": list(result.errors),
        "task_statuses": statuses,
        "stopped_by": stopped,
        "cited_paths": sorted(cited),
        "planned_paths": sorted(creates),
    }


def _task_statuses(plan_dir: Path, plan: dict) -> tuple[dict[str, str], dict[str, str] | None]:
    try:
        status_data = json.loads((plan_dir / "status.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        status_data = {}
    raw_statuses = status_data.get("tasks", {}) if isinstance(status_data, dict) else {}
    tasks = [
        str(task)
        for wave in plan.get("waves", []) if isinstance(wave, dict)
        for task in wave.get("tasks", [])
    ] if isinstance(plan, dict) else []
    statuses: dict[str, str] = {}
    stopped = None
    for task in tasks:
        entry = raw_statuses.get(task, "pending") if isinstance(raw_statuses, dict) else "pending"
        status = entry.get("status", "pending") if isinstance(entry, dict) else entry
        status = str(status or "pending")
        statuses[task] = status
        if stopped is None and status in {"blocked", "errored", "unmerged"}:
            reason = None
            if isinstance(entry, dict):
                reason = entry.get("reason") or entry.get("blocker_reason") or entry.get("error")
            stopped = {"task": task, "status": status, "reason": str(reason) if reason else None}
    return statuses, stopped


def _front_is_runnable(front: dict[str, Any]) -> bool:
    return bool(front["verified"] and front["stopped_by"] is None)


def _order_problems(queue_name: str, entries: list[dict], queues: dict, facts: dict) -> list[dict]:
    problems = []
    for index, entry in enumerate(entries):
        later_creates = {
            path for later in entries[index + 1:] for path in later["planned_paths"]
        }
        for path in sorted(set(entry["cited_paths"]) & later_creates):
            problems.append({"type": "later_plan_creates_cited_path", "plan": entry["name"], "path": path})

        own_queue_creates = {
            path
            for name in queues[queue_name] if isinstance(name, str)
            for path in facts.get(name, {}).get("planned_paths", [])
        }
        other_queue_creates = {
            path
            for other_queue, names in queues.items() if other_queue != queue_name
            for name in names if isinstance(name, str)
            for path in facts.get(name, {}).get("planned_paths", [])
        }
        for path in sorted(set(entry["cited_paths"]) & (other_queue_creates - own_queue_creates)):
            problems.append({"type": "other_queue_creates_cited_path", "plan": entry["name"], "path": path})
    return problems


def _cross_queue_warnings(queues: dict, facts: dict) -> list[dict[str, Any]]:
    warnings = []
    names = list(queues)
    for index, left_queue in enumerate(names):
        left_paths = {
            path for name in queues[left_queue] if isinstance(name, str)
            for path in facts.get(name, {}).get("cited_paths", [])
        }
        for right_queue in names[index + 1:]:
            right_paths = {
                path for name in queues[right_queue] if isinstance(name, str)
                for path in facts.get(name, {}).get("cited_paths", [])
            }
            for path in sorted(left_paths & right_paths):
                warnings.append({"queues": [left_queue, right_queue], "path": path})
    return warnings


def _lock_is_held(path: Path) -> bool:
    """Probe the advisory lock without creating a lock file or trusting stale contents."""
    try:
        with path.open("r") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                return False
    except FileNotFoundError:
        return False
