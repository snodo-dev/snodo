"""Read-only protocol endpoint for a dashboard project."""

from starlette.requests import Request
from starlette.responses import JSONResponse

from snodo.web.protocol import protocol_view


async def handler(request: Request) -> JSONResponse:
    """Return protocol details for the selected project."""
    project_name = request.query_params.get("project")
    if not project_name:
        return JSONResponse({"error": "Missing required query value: project."}, status_code=400)
    project = next((item for item in request.app.state.projects if item.name == project_name), None)
    if project is None:
        return JSONResponse({"error": "Project not found."}, status_code=404)
    result = protocol_view(project)
    return JSONResponse(result, status_code=400 if "error" in result else 200)
