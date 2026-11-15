"""Read-only endpoint for an individual task's Markdown specification."""

from __future__ import annotations


from starlette.requests import Request
from starlette.responses import JSONResponse

from snodo.web.task_spec import read_task_spec


async def handler(request: Request) -> JSONResponse:
    """Return a task spec selected by project, plan, and task query values."""
    project_name = request.query_params.get("project")
    plan_name = request.query_params.get("plan")
    task_id = request.query_params.get("task")
    if not project_name or not plan_name or not task_id:
        return JSONResponse(
            {"error": "Missing required query value: project, plan, and task are required."},
            status_code=400,
        )

    project = next(
        (item for item in request.app.state.projects if item.name == project_name),
        None,
    )
    if project is None:
        return JSONResponse({"error": "Project not found."}, status_code=404)

    markdown = read_task_spec(project.path, plan_name, task_id)
    if markdown is None:
        return JSONResponse(
            {"error": "Plan or task not found, or the name is invalid."},
            status_code=404,
        )

    return JSONResponse({
        "project": project_name,
        "plan": plan_name,
        "task": task_id,
        "markdown": markdown,
    })
