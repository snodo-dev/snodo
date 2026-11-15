"""Read-only aggregate view of validator gate send-backs across projects."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from snodo.web.gates import gate_records, summarize_gates


def build_gates(
    projects: Iterable[Any], window_days: int, now: datetime | None = None
) -> dict[str, Any]:
    """Build cross-project gate summaries and newest records."""
    all_records: list[dict[str, Any]] = []
    by_project: list[dict[str, Any]] = []
    for project in projects:
        records = gate_records(project.path, window_days, now)
        summary = summarize_gates(records)
        by_project.append({
            "name": project.name,
            "sent_back": summary["sent_back"],
            "landed_after_retry": summary["landed_after_retry"],
        })
        all_records.extend({**record, "project": project.name} for record in records)

    all_records.sort(key=lambda record: record["when"], reverse=True)
    summary = summarize_gates(all_records)
    return {
        "window_days": window_days,
        **summary,
        "by_project": by_project,
        "records": all_records[:100],
    }


async def handler(request: Any) -> JSONResponse:
    """GET endpoint returning the Gates JSON view."""
    if request.method != "GET":
        return JSONResponse({"error": "Method not allowed"}, status_code=405)
    days = request.query_params.get("days", "21")
    if days not in {"7", "21", "30"}:
        return JSONResponse({"error": "days must be 7, 21 or 30"}, status_code=400)
    result = await run_in_threadpool(
        build_gates, request.app.state.projects, int(days), datetime.now(timezone.utc)
    )
    return JSONResponse(result)
