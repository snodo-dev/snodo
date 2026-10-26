"""Path routing and Cloudflare Access assertion protection for tunnel MCP."""


class TunnelMCPPaths:
    """Serve OAuth MCP and Access-protected MCP paths through one ASGI app."""

    def __init__(self, app, verifier, *, oauth_enabled: bool, access_path: str = "mcp-st", hosts=None):
        self.app = app
        self.verifier = verifier
        self.oauth_enabled = oauth_enabled
        self.access_path = "/" + access_path.strip("/")
        self.hosts = {host.lower() for host in hosts} if hosts is not None else None

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if self.hosts is not None:
            headers = dict(scope.get("headers", []))
            host = headers.get(b"host", b"").decode("latin-1").lower()
            if host not in self.hosts:
                await _bad_host(send)
                return
            if path == "/.well-known/oauth-protected-resource":
                return await _protected_resource(send, host)
            request_host = getattr(self.verifier, "request_host", None)
            if request_host is not None:
                with request_host(host):
                    return await self._route(scope, receive, send, path)
        return await self._route(scope, receive, send, path)

    async def _route(self, scope, receive, send, path):
        if path == self.access_path or path.startswith(self.access_path + "/"):
            headers = dict(scope.get("headers", []))
            assertion = headers.get(b"cf-access-jwt-assertion", b"")
            try:
                valid = self.verifier.verify(assertion.decode("latin-1"))
            except Exception:
                valid = False
            if not valid:
                return await _refuse(send)
            scope = dict(scope)
            scope["path"] = "/mcp" + path[len(self.access_path):]
            scope["raw_path"] = scope["path"].encode("utf-8")
            return await self.app(scope, receive, send)
        if not self.oauth_enabled and (path == "/mcp" or path.startswith("/mcp/")):
            return await _refuse(send)
        if path == "/mcp" or path.startswith("/mcp/"):
            return await self._oauth_app(scope, receive, send)
        return await self.app(scope, receive, send)

    async def _oauth_app(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        authorization = headers.get(b"authorization", b"")

        async def checked_send(message):
            if message.get("type") == "http.response.start" and message.get("status") == 401 and authorization.startswith(b"Bearer "):
                try:
                    await self.verifier.verify_token(authorization[7:].decode("latin-1"))
                except Exception as error:
                    if type(error).__name__ == "OrganizationMismatchError":
                        message = dict(message)
                        message["status"] = 403
            await send(message)

        return await self.app(scope, receive, checked_send)


async def _bad_host(send):
    await send({"type": "http.response.start", "status": 421, "headers": []})
    await send({"type": "http.response.body", "body": b"Invalid host", "more_body": False})


async def _protected_resource(send, host):
    import json

    body = json.dumps({"resource": f"https://{host}/mcp"}).encode("utf-8")
    await send({"type": "http.response.start", "status": 200, "headers": [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]})
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def _refuse(send):
    await send({"type": "http.response.start", "status": 401,
                "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                            (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": b"Unauthorized", "more_body": False})


def install_tunnel_mcp_paths(mcp, verifier, *, oauth_enabled: bool, access_path: str = "mcp-st", hosts=None):
    """Wrap the MCP HTTP app factories with tunnel path routing."""
    for app_factory_name in ("streamable_http_app", "sse_app"):
        app_factory = getattr(mcp, app_factory_name)

        def tunnel_app_factory(*factory_args, _factory=app_factory, **factory_kwargs):
            return TunnelMCPPaths(
                _factory(*factory_args, **factory_kwargs), verifier,
                oauth_enabled=oauth_enabled, access_path=access_path, hosts=hosts,
            )

        setattr(mcp, app_factory_name, tunnel_app_factory)
