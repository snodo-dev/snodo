"""Read-only aggregate view of active work and items waiting across projects."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from starlette.responses import JSONResponse

from snodo.dashboard.providers import DashboardDataProvider
from snodo.tools.git import open_repo
from snodo.web.delivery import NOT_IN_MAIN, plans_delivery


def _now_timestamp(now: datetime | float) -> float:
    if isinstance(now, datetime):
        value = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        return value.timestamp()
    return float(now)


def _git_modified_files(project_path: str) -> list[str]:
    """Return tracked paths differing from HEAD, excluding untracked files."""
    with open_repo(project_path) as repo:
        output = repo.git.diff("HEAD", "--name-only")
    return sorted({name for name in output.splitlines() if name})


def _project_data(project: Any, now_timestamp: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = str(project.path)
    try:
        provider = DashboardDataProvider(path)
        plans = provider.get_plans()
        tasks = provider.get_tasks("")
        jobs = provider.get_jobs("")
        running_jobs: dict[tuple[str, str], dict[str, Any]] = {}
        for job in jobs:
            if job.get("status") != "running":
                continue
            task_ref = str(job.get("task_ref") or "")
            started = job.get("started_at")
            elapsed = max(0, int(now_timestamp - float(started))) if started is not None else 0
            row = {
                "job_id": job.get("job_id"),
                "status": "running",
                "elapsed_seconds": elapsed,
            }
            running_jobs[(task_ref, str(job.get("job_id") or ""))] = row

        active_by_plan: dict[str, list[dict[str, Any]]] = {}
        for task in tasks:
            status = task.get("status")
            ref = str(task.get("task_ref") or "")
            task_id = str(task.get("task_id") or "")
            job = next(
                (row for (job_ref, _), row in running_jobs.items()
                 if job_ref in {task_id, ref}),
                None,
            )
            if status == "in_progress" or job is not None:
                active_by_plan.setdefault(str(task.get("plan_name") or ""), []).append({
                    "task_id": task_id,
                    "status": status,
                    "job": job,
                })

        active_plans: list[dict[str, Any]] = []
        for plan in plans:
            name = str(plan.get("name") or "")
            active_tasks = active_by_plan.get(name, [])
            if not active_tasks:
                continue
            waves = plan.get("waves") or []
            statuses = plan.get("tasks") or {}
            current_wave = next(
                (index for index, wave in enumerate(waves, 1)
                 if any(statuses.get(task_id, "pending") != "completed"
                        for task_id in wave.get("tasks", []))),
                1 if waves else 0,
            )
            active_plans.append({
                "name": name,
                "wave": current_wave,
                "waves": len(waves),
                "tasks": active_tasks,
            })

        project_row = {
            "name": project.name,
            "state": "working" if any(task.get("status") == "in_progress" for task in tasks)
            or bool(running_jobs) else "idle",
            "plans": active_plans,
        }
        waiting: list[dict[str, Any]] = []
        delivery = plans_delivery(path)
        for plan in plans:
            name = str(plan.get("name") or "")
            statuses = plan.get("tasks") or {}
            if statuses and all(status == "completed" for status in statuses.values()):
                if delivery.get(name) == NOT_IN_MAIN:
                    waiting.append({"kind": "not_in_main", "project": project.name, "plan": name})

        modified = _git_modified_files(path)
        if modified:
            waiting.append({
                "kind": "uncommitted",
                "project": project.name,
                "files": modified[:10],
            })
        return project_row, waiting
    except Exception:
        # A missing or unreadable .snodo directory should not break the aggregate.
        return {"name": project.name, "state": "idle", "plans": []}, []


def build_now(projects: Iterable[Any], now: datetime | float) -> dict[str, Any]:
    """Build the current read-only summary without persisting derived state.

    This web-route response intentionally sits outside ADR 022's versioned
    ``--json`` CLI payload convention; it is not a CLI command payload.
    """
    timestamp = _now_timestamp(now)
    rows: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    for project in projects:
        row, project_waiting = _project_data(project, timestamp)
        rows.append(row)
        waiting.extend(project_waiting)
    return {"projects": rows, "waiting": waiting}


async def handler(request: Any) -> JSONResponse:
    """GET endpoint returning the current aggregate JSON view."""
    return JSONResponse(build_now(request.app.state.projects, datetime.now(timezone.utc)))
