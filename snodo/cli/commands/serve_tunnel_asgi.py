"""Path routing and Cloudflare Access assertion protection for tunnel MCP."""


class TunnelMCPPaths:
    """Serve OAuth MCP and Access-protected MCP paths through one ASGI app."""

    def __init__(self, app, verifier, *, oauth_enabled: bool, access_path: str = "mcp-st"):
        self.app = app
        self.verifier = verifier
        self.oauth_enabled = oauth_enabled
        self.access_path = "/" + access_path.strip("/")

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
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
        return await self.app(scope, receive, send)


async def _refuse(send):
    await send({"type": "http.response.start", "status": 401,
                "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                            (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": b"Unauthorized", "more_body": False})
