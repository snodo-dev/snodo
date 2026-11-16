"""Read-only aggregate view of landed work across projects."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Any, Iterable

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from snodo.web.delivery import IN_MAIN, NOT_IN_MAIN, plans_delivery
from snodo.web.flow import _type_summary, _week_zeros, flow_metrics


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
    all_durations: list[float] = []
    type_counts = {kind: 0 for kind in ("feature", "defect", "debt", "risk")}
    type_durations = {kind: [] for kind in type_counts}
    unclassified_total = 0
    weekly = _week_zeros(first_day, current.date())
    weekly_by_start = {row["week_start"]: row for row in weekly}

    for project in projects:
        metrics = flow_metrics(project.path, window_days, current)
        delivery = plans_delivery(project.path)
        landed = metrics["landed"]
        landed_total += landed
        all_durations.extend(metrics.get("_flow_seconds", []))
        unclassified_total += metrics.get("unclassified", 0)
        for kind in type_counts:
            type_counts[kind] += metrics.get("by_type", {}).get(kind, {"landed": 0})["landed"]
            type_durations[kind].extend(metrics.get("_flow_seconds_by_type", {}).get(kind, []))
        for week in metrics.get("by_week", []):
            target = weekly_by_start.get(week["week_start"])
            if target:
                for kind in type_counts:
                    target[kind] += week[kind]
                target["unclassified"] += week["unclassified"]
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
            "by_type": metrics.get("by_type", _type_summary({kind: 0 for kind in type_counts}, {kind: [] for kind in type_counts})),
            "unclassified": metrics.get("unclassified", 0),
            "by_week": metrics.get("by_week", _week_zeros(first_day, current.date())),
        })
        if landed:
            reporting_projects.append(metrics)

    totals_by_type = _type_summary(type_counts, type_durations)
    totals_median = median(all_durations) if all_durations else None
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
            "by_type": totals_by_type,
            "unclassified": unclassified_total,
            "by_week": weekly,
        },
    }


async def handler(request: Any) -> JSONResponse:
    """GET endpoint returning the Delivery JSON view."""
    if request.method != "GET":
        return JSONResponse({"error": "Method not allowed"}, status_code=405)
    days = request.query_params.get("days", "7")
    if days not in {"7", "30", "90"}:
        return JSONResponse({"error": "days must be 7, 30 or 90"}, status_code=400)
    result = await run_in_threadpool(
        build_delivery, request.app.state.projects, int(days), datetime.now(timezone.utc)
    )
    return JSONResponse(result)
