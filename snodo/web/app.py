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
from snodo.web.routes_spec import handler as spec_handler

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
        Route("/api/now/spec", spec_handler, methods=["GET", "HEAD"]),
        Route("/api/plans", plans_handler, methods=["GET", "HEAD"]),
        Route("/api/delivery", delivery_handler, methods=["GET", "HEAD"]),
        Route("/api/gates", gates_handler, methods=["GET", "HEAD"]),
        Route("/", _index_handler, methods=["GET", "HEAD"]),
        Mount("/static", app=StaticFiles(directory=_STATIC_DIR, check_dir=False), name="static"),
    ]
    app = Starlette(routes=routes, middleware=[Middleware(_RequestGuard)])
    app.state.projects = list(projects)
    return app


#: Where a dashboard started without --port begins looking for a free port.
#: Kept clear of the `snodo serve` range (55441 and up) so the two never compete.
DASHBOARD_PORT_START = 44331

#: How many consecutive ports a no---port dashboard tries before giving up.
_PORT_SCAN_ATTEMPTS = 64


def _bind_socket(port: int | None = None) -> socket.socket:
    """Bind loopback at the requested port, or at the first free port from 44331 up."""
    if port is not None:
        return _bind_one(port)
    for candidate in range(DASHBOARD_PORT_START, DASHBOARD_PORT_START + _PORT_SCAN_ATTEMPTS):
        try:
            return _bind_one(candidate)
        except RuntimeError:
            continue
    raise RuntimeError(
        f"No free dashboard port in {DASHBOARD_PORT_START}-"
        f"{DASHBOARD_PORT_START + _PORT_SCAN_ATTEMPTS - 1}; pass --port to choose one"
    )


def _bind_one(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError as exc:
        sock.close()
        raise RuntimeError(f"Port {port} is in use") from exc
    sock.listen(socket.SOMAXCONN)
    return sock


def serve(projects: Iterable[Project], port: int | None = None) -> None:
    """Run the dashboard on loopback on the first free port from 44331 upward by default."""
    sock = _bind_socket(port)
    selected_port = sock.getsockname()[1]
    print(f"Serving Snodo dashboard at http://127.0.0.1:{selected_port}")
    config = uvicorn.Config(create_app(projects), host="127.0.0.1", port=selected_port)
    server = uvicorn.Server(config)
    server.run(sockets=[sock])
