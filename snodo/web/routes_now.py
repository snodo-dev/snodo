"""Read-only aggregate view of active work and items waiting across projects."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from snodo.dashboard.providers import DashboardDataProvider
from snodo.tools.git import open_repo
from snodo.web.delivery import NOT_IN_MAIN, plans_delivery
from snodo.web.now_counters import project_now_counters
from snodo.web.now_tree import project_now_tree


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


def _project_data(project: Any, now: datetime | float) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    path = str(project.path)
    try:
        counters = project_now_counters(path, now)
        tree = project_now_tree(path, now)
        def task_row(task: dict[str, Any]) -> dict[str, Any]:
            return {"task_id": task["task_id"], "status": task["status"],
                    "job": {"job_id": task["job_id"]} if task["job_id"] else None,
                    "elapsed_seconds": task["elapsed_seconds"]}
        row = {"name": project.name, "liveness": tree["liveness"],
               "last_activity_at": _iso_timestamp(tree["last_activity"]),
               "plans": [{"name": plan["name"], "wave": plan["current_wave"],
                          "waves": plan["total_waves"], "last_activity_at": _iso_timestamp(plan["last_activity"]),
                          "wave_groups": [{"id": wave["number"], "total": wave["task_total"],
                                           "status_counts": wave["status_counts"],
                                           "tasks": [task_row(t) for t in wave["tasks"]]} for wave in plan["waves"]]}
                         for plan in tree["plans"]],
               "outside_plan": [task_row(task) for task in tree["outside_plan"]]}
    except Exception:
        counters = {}
        row = {"name": project.name, "liveness": "no_recent_signal", "last_activity_at": None,
               "plans": [], "outside_plan": []}
    waiting = []
    try:
        plans = DashboardDataProvider(path).get_plans()
        delivery = plans_delivery(path)
        for plan in plans:
            name = str(plan.get("name") or "")
            statuses = plan.get("tasks") or {}
            if statuses and all(status == "completed" for status in statuses.values()) and delivery.get(name) == NOT_IN_MAIN:
                waiting.append({"kind": "not_in_main", "project": project.name, "plan": name})
        modified = _git_modified_files(path)
        if modified:
            waiting.append({"kind": "uncommitted", "project": project.name, "files": modified[:10]})
    except Exception:
        waiting = []
    return row, counters, waiting


def _iso_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    return str(value)


def _sum_counter(total: int | None, value: Any) -> int | None:
    return (total or 0) + value if isinstance(value, int) else total


def build_now(projects: Iterable[Any], now: datetime | float) -> dict[str, Any]:
    """Build the read-only 24-hour activity summary."""
    rows, waiting = [], []
    counters: dict[str, Any] = {"projects_active": 0, "plans": 0, "queues": None,
                                "waves": 0, "tasks": {"count": 0, "by_status": {}}, "recons": None, "commits": None}
    for project in projects:
        row, values, pending = _project_data(project, now)
        rows.append(row)
        waiting.extend(pending)
        counters["projects_active"] += values.get("active") is True
        pq = values.get("plans_queues") or {}
        counters["plans"] += pq.get("plans", 0) if isinstance(pq.get("plans"), int) else 0
        counters["queues"] = _sum_counter(counters["queues"], pq.get("queues"))
        counters["waves"] += values.get("waves", 0) if isinstance(values.get("waves"), int) else 0
        task_data = values.get("tasks") or {}
        counters["tasks"]["count"] += task_data.get("total", 0) if isinstance(task_data.get("total"), int) else 0
        for status, count in task_data.get("by_status", {}).items():
            counters["tasks"]["by_status"][status] = counters["tasks"]["by_status"].get(status, 0) + count
        recons = values.get("recons")
        if isinstance(recons, dict) and isinstance(recons.get("count"), int):
            if counters["recons"] is None:
                counters["recons"] = {"count": 0, "running": 0, "completed": 0}
            for key in counters["recons"]:
                counters["recons"][key] += recons.get(key, 0)
        counters["commits"] = _sum_counter(counters["commits"], values.get("commits"))
    rows.sort(key=lambda item: item["last_activity_at"] or "", reverse=True)
    generated = _iso_timestamp(now)
    return {"window_hours": 24, "generated_at": generated, "counters": counters, "projects": rows, "waiting": waiting}


async def handler(request: Any) -> JSONResponse:
    """GET endpoint returning the current aggregate JSON view."""
    result = await run_in_threadpool(
        build_now, request.app.state.projects, datetime.now(timezone.utc)
    )
    return JSONResponse(result)
