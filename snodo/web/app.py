"""Read-only, loopback-only Starlette application for the local dashboard."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Callable, Iterable

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from snodo.web.projects import Project
from snodo.web.routes_delivery import handler as delivery_handler
from snodo.web.routes_gates import handler as gates_handler
from snodo.web.routes_now import handler as now_handler
from snodo.web.routes_plans import handler as plans_handler

_STATIC_DIR = Path(__file__).resolve().parent / "static"


class _RequestGuard:
    """Reject non-read methods and non-loopback Host headers."""

    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode("latin-1").lower()
        if not _is_loopback_host(host):
            response: Response = PlainTextResponse("Invalid Host header", status_code=400)
        elif scope["method"] not in {"GET", "HEAD"}:
            response = PlainTextResponse("Method not allowed", status_code=405)
        else:
            if scope["method"] == "HEAD":
                scope = {**scope, "method": "GET"}

                async def send_without_body(message: dict) -> None:
                    if message["type"] == "http.response.body":
                        message = {**message, "body": b""}
                    await send(message)

                await self.app(scope, receive, send_without_body)
            else:
                await self.app(scope, receive, send)
            return
        await response(scope, receive, send)


def _is_loopback_host(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "[::1]"}:
        return True
    if host.startswith("localhost:") or host.startswith("127.0.0.1:"):
        port = host.rpartition(":")[2]
        return port.isdigit()
    if host.startswith("[::1]:"):
        return host[6:].isdigit()
    return False


async def _projects_handler(request: Request) -> JSONResponse:
    return JSONResponse([
        {"name": project.name, "path": str(project.path)}
        for project in request.app.state.projects
    ])


async def _index_handler(request: Request) -> Response:
    index = _STATIC_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    return PlainTextResponse("Dashboard files are missing.")


def create_app(projects: Iterable[Project]) -> Starlette:
    """Create the local dashboard application for the supplied projects."""
    routes = [
        Route("/api/projects", _projects_handler, methods=["GET", "HEAD"]),
        Route("/api/now", now_handler, methods=["GET", "HEAD"]),
        Route("/api/plans", plans_handler, methods=["GET", "HEAD"]),
        Route("/api/delivery", delivery_handler, methods=["GET", "HEAD"]),
        Route("/api/gates", gates_handler, methods=["GET", "HEAD"]),
        Route("/", _index_handler, methods=["GET", "HEAD"]),
        Mount("/static", app=StaticFiles(directory=_STATIC_DIR, check_dir=False), name="static"),
    ]
    app = Starlette(routes=routes, middleware=[Middleware(_RequestGuard)])
    app.state.projects = list(projects)
    return app


def _select_port(is_port_free: Callable[[int], bool], port: int | None = None) -> int:
    """Return an available requested port, or the first of 20 default ports."""
    if port is not None:
        if not is_port_free(port):
            raise RuntimeError(f"Port {port} is already in use")
        return port
    for candidate in range(55441, 55461):
        if is_port_free(candidate):
            return candidate
    raise RuntimeError("No free dashboard port found in range 55441-55460")


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def serve(projects: Iterable[Project], port: int | None = None) -> None:
    """Run the dashboard on loopback, choosing a free default port if needed."""
    selected_port = _select_port(_port_is_free, port)
    print(f"Serving Snodo dashboard at http://127.0.0.1:{selected_port}")
    uvicorn.run(create_app(projects), host="127.0.0.1", port=selected_port)
