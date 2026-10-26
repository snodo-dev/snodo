"""Path routing and Cloudflare Access assertion protection for tunnel MCP."""

from snodo.infrastructure.oauth_verifier import OrganizationMismatchError


class TunnelMCPPaths:
    """Serve OAuth MCP and Access-protected MCP paths through one ASGI app."""

    def __init__(self, app, verifier, *, oauth_enabled: bool, access_path: str = "mcp-st", hosts=None, private_host=None, issuer_url=None):
        self.app = app
        self.verifier = verifier
        self.oauth_enabled = oauth_enabled
        self.access_path = "/" + access_path.strip("/")
        self.hosts = {host.lower() for host in hosts} if hosts is not None else None
        self.private_host = private_host.lower() if private_host else None
        self.issuer_url = issuer_url

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if self.hosts is not None:
            headers = dict(scope.get("headers", []))
            host = headers.get(b"host", b"").decode("latin-1").lower()
            loopback_host = host.startswith(("localhost:", "127.0.0.1:", "[::1]:"))
            if host not in self.hosts and not loopback_host:
                await _bad_host(send)
                return
            effective_host = (
                self.private_host
                if self.private_host and host.startswith(("localhost:", "127.0.0.1:", "[::1]:"))
                else host
            )
            if path in (
                "/.well-known/oauth-protected-resource",
                "/.well-known/oauth-protected-resource/mcp",
            ):
                return await _protected_resource(send, effective_host, self.issuer_url)
            request_host = getattr(self.verifier, "request_host", None)
            if request_host is not None:
                with request_host(effective_host):
                    return await self._route(scope, receive, send, path, effective_host)
            return await self._route(scope, receive, send, path, effective_host)
        return await self._route(scope, receive, send, path)

    async def _route(self, scope, receive, send, path, request_host=None):
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
        return await self._oauth_app(scope, receive, send, request_host=request_host)

    async def _oauth_app(self, scope, receive, send, *, request_host=None):
        headers = dict(scope.get("headers", []))
        authorization = headers.get(b"authorization", b"")
        if authorization.startswith(b"Bearer ") and callable(getattr(self.verifier, "verify_token", None)):
            try:
                await self.verifier.verify_token(authorization[7:].decode("latin-1"))
            except OrganizationMismatchError:
                return await _forbidden(send)
        if request_host is None or self.hosts is None:
            return await self.app(scope, receive, send)

        async def rewrite_challenge(message):
            if message.get("type") == "http.response.start" and message.get("status") == 401:
                headers = []
                for name, value in message.get("headers", []):
                    if name.lower() == b"www-authenticate":
                        value = _rewrite_resource_metadata(value, self.hosts, request_host)
                    headers.append((name, value))
                message = dict(message, headers=headers)
            await send(message)

        return await self.app(scope, receive, rewrite_challenge)


def _rewrite_resource_metadata(value, known_hosts, request_host):
    import re

    pattern = re.compile(rb'(resource_metadata=["\'])https://([^/"\']+)(/\.well-known/oauth-protected-resource/mcp)(["\'])', re.IGNORECASE)

    def replace(match):
        metadata_host = match.group(2).decode("latin-1").lower()
        if metadata_host not in known_hosts:
            return match.group(0)
        return match.group(1) + b"https://" + request_host.encode("latin-1") + match.group(3) + match.group(4)

    return pattern.sub(replace, value)


async def _forbidden(send):
    await send({"type": "http.response.start", "status": 403, "headers": [
        (b"content-type", b"text/plain; charset=utf-8"),
        (b"cache-control", b"no-store"),
    ]})
    await send({"type": "http.response.body", "body": b"Forbidden", "more_body": False})


async def _bad_host(send):
    await send({"type": "http.response.start", "status": 421, "headers": []})
    await send({"type": "http.response.body", "body": b"Invalid host", "more_body": False})


async def _protected_resource(send, host, issuer_url=None):
    import json

    from mcp.shared.auth import ProtectedResourceMetadata
    from pydantic import AnyHttpUrl

    metadata = ProtectedResourceMetadata(
        resource=AnyHttpUrl(f"https://{host}/mcp"),
        authorization_servers=[AnyHttpUrl(str(issuer_url or f"https://{host}"))],
    )
    document = metadata.model_dump(mode="json", exclude_none=True)
    if not issuer_url:
        document.pop("authorization_servers", None)
    body = json.dumps(document).encode("utf-8")
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


def install_tunnel_mcp_paths(mcp, verifier, *, oauth_enabled: bool, access_path: str = "mcp-st", hosts=None, private_host=None, issuer_url=None):
    """Wrap the MCP HTTP app factories with tunnel path routing."""
    for app_factory_name in ("streamable_http_app", "sse_app"):
        app_factory = getattr(mcp, app_factory_name)

        def tunnel_app_factory(*factory_args, _factory=app_factory, **factory_kwargs):
            return TunnelMCPPaths(
                _factory(*factory_args, **factory_kwargs), verifier,
                oauth_enabled=oauth_enabled, access_path=access_path, hosts=hosts,
                private_host=private_host,
                issuer_url=issuer_url,
            )

        setattr(mcp, app_factory_name, tunnel_app_factory)
