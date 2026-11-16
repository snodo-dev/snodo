"""Read-only identity endpoint for the local dashboard."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from snodo import project

_logger = logging.getLogger(__name__)


def _identity(path: Path) -> dict[str, str]:
    """Resolve the visible identity without reading or writing Snodo state."""
    fallback_name = path.name or str(path)
    try:
        project_id, scope = project.resolve_project_id(str(path))
        if scope == "remote" and project_id:
            return {
                "name": project_id.rstrip("/").rsplit("/", 1)[-1],
                "remote": project_id,
                "project_id": project_id,
            }
        if scope == "local" and project_id.startswith("local:"):
            return {"name": fallback_name, "remote": "local", "project_id": project_id}
    except Exception as exc:
        _logger.debug("Failed to resolve dashboard project identity: %s", exc)
    return {"name": fallback_name, "remote": "local", "project_id": "local"}


async def handler(request: Any) -> JSONResponse:
    """Return the started project's identity, resolving Git off the event loop."""
    projects = request.app.state.projects
    path = Path(projects[0].path) if projects else Path.cwd()
    return JSONResponse(await run_in_threadpool(_identity, path))
