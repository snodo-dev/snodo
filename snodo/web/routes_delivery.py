"""Read-only aggregate view of landed work across projects."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Any, Iterable

from starlette.responses import JSONResponse

from snodo.web.delivery import IN_MAIN, NOT_IN_MAIN, plans_delivery
from snodo.web.flow import flow_metrics


def build_delivery(
    projects: Iterable[Any], window_days: int, now: datetime | None = None
) -> dict[str, Any]:
    """Build the cross-project Delivery summary without persisting derived state."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    first_day = current.date() - timedelta(days=window_days - 1)
    by_day = {
        (first_day + timedelta(days=offset)).isoformat(): 0
        for offset in range(window_days)
    }
    rows: list[dict[str, Any]] = []
    landed_total = 0
    flow_load_total = 0
    not_in_main_total = 0
    reporting_projects: list[dict[str, Any]] = []

    for project in projects:
        metrics = flow_metrics(project.path, window_days, current)
        delivery = plans_delivery(project.path)
        landed = metrics["landed"]
        landed_total += landed
        flow_load_total += metrics["flow_load"]
        not_in_main_total += sum(status == NOT_IN_MAIN for status in delivery.values())
        for item in metrics["landed_by_day"]:
            day = item["date"]
            if day in by_day:
                by_day[day] += item["landed"]
        rows.append({
            "name": project.name,
            "landed": landed,
            "plans_in_main": sum(status == IN_MAIN for status in delivery.values()),
            "median_flow_seconds": metrics["median_flow_seconds"],
            "first_try_rate": metrics["first_try_rate"],
            "flow_load": metrics["flow_load"],
        })
        if landed:
            reporting_projects.append(metrics)

    totals_median = (
        median(item["median_flow_seconds"] for item in reporting_projects)
        if reporting_projects and all(item["median_flow_seconds"] is not None for item in reporting_projects)
        else None
    )
    totals_first_try = (
        mean(item["first_try_rate"] for item in reporting_projects)
        if reporting_projects and all(item["first_try_rate"] is not None for item in reporting_projects)
        else None
    )
    return {
        "window_days": window_days,
        "projects": rows,
        "by_day": [{"date": day, "landed": count} for day, count in by_day.items()],
        "totals": {
            "landed": landed_total,
            "flow_load": flow_load_total,
            "plans_not_in_main": not_in_main_total,
            "median_flow_seconds": totals_median,
            "first_try_rate": totals_first_try,
        },
    }


async def handler(request: Any) -> JSONResponse:
    """GET endpoint returning the Delivery JSON view."""
    if request.method != "GET":
        return JSONResponse({"error": "Method not allowed"}, status_code=405)
    days = request.query_params.get("days", "7")
    if days not in {"7", "30"}:
        return JSONResponse({"error": "days must be 7 or 30"}, status_code=400)
    return JSONResponse(
        build_delivery(request.app.state.projects, int(days), datetime.now(timezone.utc))
    )
