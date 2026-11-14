"""Read-only aggregate view of plans across dashboard projects."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from starlette.responses import JSONResponse

from snodo.dashboard.providers import DashboardDataProvider
from snodo.web.delivery import plans_delivery


def build_plans(projects: Iterable[Any]) -> list[dict[str, Any]]:
    """Build plan progress rows without modifying project state."""
    rows: list[tuple[float | None, dict[str, Any]]] = []
    for project in projects:
        path = str(project.path)
        try:
            plans = DashboardDataProvider(path).get_plans()
        except Exception:
            plans = []
        try:
            delivery = plans_delivery(path)
        except Exception:
            delivery = {}
        for plan in plans:
            name = str(plan.get("name") or "")
            tasks = plan.get("tasks")
            if not isinstance(tasks, dict):
                tasks = {}
            try:
                modified = (
                    project.path / ".snodo" / "plans" / name / "status.json"
                ).stat().st_mtime
                updated_at = datetime.fromtimestamp(modified, timezone.utc).isoformat()
            except (OSError, TypeError, ValueError):
                modified = None
                updated_at = None
            rows.append((modified, {
                "project": project.name,
                "name": name,
                "intent": plan.get("intent", ""),
                "tasks_total": len(tasks),
                "tasks_completed": sum(status == "completed" for status in tasks.values()),
                "running": any(status == "in_progress" for status in tasks.values()),
                "delivery": delivery.get(name),
                "updated_at": updated_at,
            }))
    rows.sort(key=lambda item: (
        item[0] is None,
        -(item[0] or 0),
        item[1]["project"],
        item[1]["name"],
    ))
    return [row for _, row in rows]


async def handler(request: Any) -> JSONResponse:
    """GET endpoint returning plans as JSON."""
    return JSONResponse(build_plans(request.app.state.projects))
