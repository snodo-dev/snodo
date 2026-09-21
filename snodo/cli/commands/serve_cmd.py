"""Serve command - Start MCP server from protocol definition.

FILE: snodo/cli/commands/serve_cmd.py
"""

import collections
import hashlib
import io
import json
import logging
import os
import random
import re
import signal
import string
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

import typer

from snodo.cli.commands import load_protocol

_logger = logging.getLogger(__name__)

#: Where a server with no --port starts looking for a free port. Kept at the
#: historical fixed default so the first server on a machine still lands on the
#: port operators expect, and a second one walks upward from there.
DEFAULT_PORT = 55441

AUTH_METHODS = ("oauth", "service-token")

#: How many consecutive ports a no---port server will try before giving up.
_PORT_SCAN_ATTEMPTS = 64


def _auth_methods(values: Optional[list[str]]) -> list[str]:
    """Return the selected tunnel mechanisms, preserving repeat order."""
    if not values:
        return ["oauth"]
    invalid = [value for value in values if value not in AUTH_METHODS]
    if invalid:
        raise ValueError(
            f"invalid auth value {invalid[0]!r}; choose oauth or service-token"
        )
    return list(dict.fromkeys(values))


def _provision_with_auth(api_key: str, project_slug: str, mode: str,
                         short_id: str, version: str, port: int,
                         auth_methods: list[str]) -> dict:
    """Provision with an explicit auth set, tolerating legacy test doubles."""
    # OAuth is the historical default. Keeping its call shape unchanged also
    # lets older integrations that wrap the provisioning helper continue to
    # work while the helper itself sends the explicit default to the API.
    if auth_methods == ["oauth"]:
        return _provision_tunnel(api_key, project_slug, mode, short_id, version, port)
    return _provision_tunnel(
        api_key, project_slug, mode, short_id, version, port, auth=auth_methods
    )


def register(app: typer.Typer) -> None:
    """Register top-level CLI commands onto app (called by discovery loop)."""

    @app.command()
    def serve(
        protocol: str = typer.Option(
            ".snodo/protocol.yml", "--protocol", help="Path to protocol file",
        ),
        mode: Optional[str] = typer.Option(
            None, "--mode", help="Serve a single mode (default: all modes)",
        ),
        transport: str = typer.Option(
            "stdio", "--transport", help="Transport type: stdio, sse, or streamable-http",
        ),
        port: Optional[int] = typer.Option(
            None, "--port",
            help="Port for SSE/streamable-http transport (default: find a free one)",
        ),
        tunnel: bool = typer.Option(
            False, "--tunnel", help="Provision a managed Cloudflare tunnel (requires free snodo account)",
        ),
        auth: Optional[list[str]] = typer.Option(
            None, "--auth",
            help="Tunnel authentication (oauth or service-token); repeat for any accepted mechanism",
        ),
        rotate: bool = typer.Option(
            False, "--rotate", help="Rotate the Cloudflare service token for an existing tunnel",
        ),
        delete: bool = typer.Option(
            False, "--delete", help="Deprovision and remove the managed tunnel",
        ),
        hostname: Optional[str] = typer.Option(
            None, "--hostname",
            help="With --delete: deprovision a tunnel by hostname, even if this "
                 "project's local config does not record it (e.g. provisioned out of band)",
        ),
        mcp_install: bool = typer.Option(
            False, "--mcp-install", help="Install MCP servers into Claude Desktop config",
        ),
        mcp_uninstall: bool = typer.Option(
            False, "--mcp-uninstall", help="Remove this project's MCP entries",
        ),
        mcp_uninstall_all: bool = typer.Option(
            False, "--mcp-uninstall-all", help="Remove ALL snodo MCP entries",
        ),
        mcp_list: bool = typer.Option(
            False, "--mcp-list", help="List registered snodo MCP entries",
        ),
        purge: bool = typer.Option(
            False, "--purge", help="Also delete .snodo/ directory and sessions",
        ),
        orphans: bool = typer.Option(
            False, "--orphans", help="Detect and remove orphan MCP entries",
        ),
        yes: bool = typer.Option(
            False, "--yes", "-y", help="Skip confirmation prompts",
        ),
        install: bool = typer.Option(
            False, "--install", help="Deprecated alias for --mcp-install",
        ),
        uninstall: bool = typer.Option(
            False, "--uninstall", help="Deprecated alias for --mcp-uninstall",
        ),
        uninstall_all: bool = typer.Option(
            False, "--uninstall-all", help="Deprecated alias for --mcp-uninstall-all",
        ),
        project_name: Optional[str] = typer.Option(
            None, "--project-name", help="Override project name for MCP entry naming",
        ),
    ):
        """Start MCP server from protocol definition."""
        args = SimpleNamespace(
            protocol=protocol, mode=mode, transport=transport, port=port,
            tunnel=tunnel, auth=auth, rotate=rotate, delete=delete, hostname=hostname,
            mcp_install=mcp_install, mcp_uninstall=mcp_uninstall,
            mcp_uninstall_all=mcp_uninstall_all, mcp_list=mcp_list,
            purge=purge, orphans=orphans,
            yes=yes, install=install, uninstall=uninstall,
            uninstall_all=uninstall_all, project_name=project_name,
        )
        return serve_command(args)




def _derive_project_root(protocol_path: str) -> str:
    """Derive project root from the protocol file path.

    If the protocol lives at <project>/.snodo/protocol.yml, the project
    root is <project>. Otherwise, the parent directory of the protocol file.

    Args:
        protocol_path: Path to protocol YAML file (absolute or relative)

    Returns:
        Absolute path to project root directory
    """
    path = Path(protocol_path).resolve()
    if path.parent.name == ".snodo":
        return str(path.parent.parent)
    return str(path.parent)


def serve_command(args) -> int:
    """Start MCP server from protocol definition."""
    try:
        _auth_methods(getattr(args, "auth", None))
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    if (
        getattr(args, "mcp_install", False)
        or getattr(args, "mcp_uninstall", False)
        or getattr(args, "mcp_uninstall_all", False)
        or getattr(args, "mcp_list", False)
    ):
        return _handle_mcp_command(args)

    protocol_path = Path(args.protocol)
    protocol = load_protocol(protocol_path)
    if not protocol:
        return 1

    if getattr(args, "install", False):
        print("Note: 'snodo serve --install' is deprecated. "
              "Use 'snodo serve --mcp-install' instead.", file=sys.stderr)
        return _handle_install(args, protocol, protocol_path)

    if getattr(args, "uninstall_all", False):
        print("Note: 'snodo serve --uninstall-all' is deprecated. "
              "Use 'snodo serve --mcp-uninstall-all' instead.", file=sys.stderr)
        return _handle_uninstall_all()

    if getattr(args, "uninstall", False):
        print("Note: 'snodo serve --uninstall' is deprecated. "
              "Use 'snodo serve --mcp-uninstall' instead.", file=sys.stderr)
        return _handle_uninstall(args, protocol, protocol_path)

    if getattr(args, "tunnel", False):
        return _run_tunnel(args, protocol, protocol_path)

    return _run_server(args, protocol)


def _handle_mcp_command(args) -> int:
    """Run MCP client configuration operations from the serve command."""
    from snodo.cli.commands.install_cmd import (
        install_command, list_command, uninstall_command,
    )

    if getattr(args, "mcp_install", False):
        return install_command(SimpleNamespace(
            protocol=args.protocol,
            project_name=getattr(args, "project_name", None),
        ))

    if getattr(args, "mcp_list", False):
        return list_command()

    return uninstall_command(SimpleNamespace(
        protocol=args.protocol,
        mode=getattr(args, "mode", None),
        all_entries=getattr(args, "mcp_uninstall_all", False),
        purge=getattr(args, "purge", False),
        orphans=getattr(args, "orphans", False),
        yes=getattr(args, "yes", False),
    ))


def _run_server(args, protocol) -> int:
    """Create and run the MCP server with FastMCP transport."""
    from snodo.mcp.server import ProtocolMCPServer
    from snodo.mcp.transport import build_fastmcp_server

    project_root = _derive_project_root(args.protocol)
    mode_id = args.mode
    transport = args.transport

    if mode_id and not protocol.get_mode(mode_id):
        available = ", ".join(m.mode_id for m in protocol.modes)
        print(f"Error: Mode '{mode_id}' not found. Available: {available}", file=sys.stderr)
        return 1

    try:
        protocol_server = ProtocolMCPServer(
            protocol=protocol,
            project_root=project_root,
            mode_id=mode_id,
        )
    except Exception as e:
        print(f"Error: Failed to create MCP server: {e}", file=sys.stderr)
        return 1

    # Detect tunnel mode and wire the selected bearer verifier if active.
    tunnel_config = _load_tunnel_config(project_root)
    tunnel_hostname = tunnel_config.get("hostname")
    auth_methods = _auth_methods(
        getattr(args, "auth", None) or tunnel_config.get("auth")
    )
    extra_kwargs = {}
    if tunnel_hostname and "oauth" in auth_methods:
        from snodo.infrastructure.jwks import JwksClient
        from snodo.infrastructure.oauth_verifier import JwksTokenVerifier
        from mcp.server.auth.settings import AuthSettings
        from pydantic import AnyHttpUrl

        jwks = JwksClient()
        if jwks.fetch():
            resource = f"https://{tunnel_hostname}/mcp"
            extra_kwargs["token_verifier"] = JwksTokenVerifier(jwks, resource=resource)
            # The resource identifier is the canonical URI of the MCP server,
            # which is the endpoint clients actually call — FastMCP serves
            # streamable-http at streamable_http_path, default "/mcp". Passing
            # the bare host instead made pydantic normalise it to
            # "https://<host>/", so the protected-resource metadata advertised
            # the origin while the server lived at /mcp. A client that checks a
            # token against the endpoint it was configured with then sees a
            # resource it never agreed to.
            extra_kwargs["auth_settings"] = AuthSettings(
                issuer_url=AnyHttpUrl("https://mcp-auth.snodo.dev"),
                resource_server_url=AnyHttpUrl(resource),
            )
            print("  OAuth 2.1 enabled (RS256 JWTs from mcp-auth.snodo.dev)", file=sys.stderr)
        else:
            print("  Warning: OAuth 2.1 disabled — could not load JWKS", file=sys.stderr)

    mcp = build_fastmcp_server(protocol_server, **extra_kwargs)
    tools = protocol_server.get_tools()
    mode_label = mode_id or "all"

    # A non-stdio server owns a port. An explicit --port is the port the
    # operator meant: if a listener already owns it, name the holder and how
    # long it has been there instead of surfacing a raw bind error (Fixes #290)
    # and never silently move it — whatever is configured to reach that port
    # meant it. With no --port, find a port that is free and say which, so any
    # server can start alongside any other — same project, different mode, or
    # a different project entirely (Fixes #309). The check is read-only
    # (lsof/psutil); it never binds, so it cannot itself become the stale
    # holder it reports.
    explicit_port = getattr(args, "port", None)
    raw_tunnel_port = tunnel_config.get("port")
    try:
        tunnel_port = int(raw_tunnel_port) if raw_tunnel_port is not None else None
    except (ValueError, TypeError):
        tunnel_port = None

    if transport != "stdio":
        requested_port = explicit_port if explicit_port is not None else tunnel_port
        port = _choose_serve_port(requested_port)
        if port is None:
            return 1
        if tunnel_port is not None and port != tunnel_port:
            print(
                f"Error: {_tunnel_port_mismatch_explanation(port, tunnel_port, tunnel_hostname)}",
                file=sys.stderr,
            )
            return 1
        if explicit_port is None and tunnel_port is not None:
            print(f"Using recorded tunnel port {port}", file=sys.stderr)
        mcp.settings.port = port
    else:
        port = explicit_port
        if port is not None:
            mcp.settings.port = port

    # Accept proxied requests when not using stdio
    if transport != "stdio":
        os.environ["FORWARDED_ALLOW_IPS"] = "*"
        mcp.settings.transport_security.enable_dns_rebinding_protection = False

    print(
        f"Snodo MCP [{protocol.protocol_id}] mode={mode_label} "
        f"tools={len(tools)} transport={transport}",
        file=sys.stderr,
    )

    if transport != "stdio" and not tunnel_hostname:
        print()
        print("To expose this server remotely, use your own tunneling tool:")
        print(f"  ngrok:        ngrok http {port}")
        print(f"  cloudflared:  cloudflared tunnel --url http://localhost:{port}")
        print(f"  tailscale:    tailscale funnel {port}")
        print()
        print("  Or use: snodo serve --tunnel (requires free snodo account)")

    try:
        mcp.run(transport=transport)
    except SystemExit as e:
        # uvicorn reports a bind failure by logging the OSError and exiting
        # with STARTUP_FAILURE (3). The port can be taken between the check
        # above and this bind; explain that failure rather than re-raising the
        # bare SystemExit. Any other exit code is not ours to reinterpret.
        if transport != "stdio" and e.code == 3:
            print(f"Error: {_port_in_use_explanation(port)}", file=sys.stderr)
            return 1
        raise
    return 0


def _handle_install(args, protocol, protocol_path) -> int:
    """Install MCP servers into Claude Desktop config."""
    from snodo.mcp.installer import (
        install, get_claude_config_path, print_install_result
    )

    abs_protocol_path = str(protocol_path.resolve())
    project_name = getattr(args, "project_name", None)
    try:
        config_path = get_claude_config_path()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    added, updated = install(protocol, abs_protocol_path, project_name, config_path)
    print_install_result(added, updated, config_path)
    return 0


def _handle_uninstall_all() -> int:
    """Remove all snodo-managed MCP entries."""
    from snodo.mcp.installer import (
        uninstall_all, get_claude_config_path, print_uninstall_result
    )

    try:
        config_path = get_claude_config_path()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    removed = uninstall_all(config_path)
    print_uninstall_result(removed, config_path)
    return 0


def _handle_uninstall(args, protocol, protocol_path) -> int:
    """Remove this project's MCP entries."""
    from snodo.mcp.installer import (
        uninstall, get_claude_config_path, print_uninstall_result
    )

    abs_protocol_path = str(protocol_path.resolve())
    project_name = getattr(args, "project_name", None)
    try:
        config_path = get_claude_config_path()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    removed = uninstall(protocol, abs_protocol_path, project_name, args.mode, config_path)
    print_uninstall_result(removed, config_path)
    return 0


# ------------------------------------------------------------------#
# Managed tunnel via snodo.dev
# ------------------------------------------------------------------#


class TunnelAPIError(RuntimeError):
    """A tunnel worker request failed.

    *status_code* is the HTTP status the tunnel worker actually answered
    with (``None`` for transport-level failures), so callers can tell an
    authentication rejection apart from every other error instead of
    blaming the API key for all of them.

    *existing_hostname* is set on a 409 conflict: the cloud's
    one-tunnel-per-project-and-mode rule names the tunnel that blocks
    provisioning, and the operator needs that hostname to act on it.
    """

    def __init__(self, message: str, status_code: Optional[int] = None,
                 existing_hostname: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.existing_hostname = existing_hostname


# Managed tunnel hostnames look like "<slug>-<mode>-<short>.tunnel.snodo.dev";
# the name before the tunnel domain is ONE DNS label (the wildcard
# certificate covers exactly one label, and a dot is a label separator, not
# a character). The 409 body may carry it as JSON or inside free-form error
# text. Embedded dots are deliberately NOT matched: a hostname carrying one
# is the broken shape (issue #308), never a name to act on as valid.
_TUNNEL_HOSTNAME_RE = re.compile(
    r"\b[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.tunnel\.snodo\.dev\b",
    re.IGNORECASE,
)


def _extract_existing_hostname(body: str) -> Optional[str]:
    """Pull the blocking tunnel hostname out of a 409 provisioning body."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        data = None
    if isinstance(data, dict):
        host = data.get("hostname")
        if isinstance(host, str) and host.strip():
            return host.strip()
    match = _TUNNEL_HOSTNAME_RE.search(body)
    return match.group(0) if match else None


# The cloud's tunnel hostnames sit directly under tunnel.snodo.dev: the
# wildcard certificate covers exactly the one label there. A leading portion
# with an embedded dot (provisioned before the slug was constrained to one
# label — issue #308) names something one level deeper, which no certificate
# can ever match, so its clients are always refused.
_TUNNEL_DOMAIN_SUFFIX = ".tunnel.snodo.dev"


def _warn_if_unservable_hostname(hostname: str) -> bool:
    """Tell the operator when a recorded hostname can never serve TLS.

    Returns True if the hostname is not a single DNS label under the tunnel
    domain — the shape produced by a dotted project directory before the slug
    was constrained. Such a tunnel is unreachable; it is not load-bearing,
    but it does exist on the cloud side and the operator otherwise has no way
    to see it. The message names why and points at the existing, deliberate
    delete-by-hostname path (which is unchanged here).
    """
    host = (hostname or "").strip().rstrip(".").lower()
    if not host.endswith(_TUNNEL_DOMAIN_SUFFIX):
        return False
    leading = host[: -len(_TUNNEL_DOMAIN_SUFFIX)]
    if "." not in leading:
        return False
    print(f"Warning: the tunnel at {host!r} has a hostname that is more than "
          "one DNS label before tunnel.snodo.dev.", file=sys.stderr)
    print("  The wildcard certificate covers exactly one label there, so TLS "
          "can never match this name and clients will always be refused.",
          file=sys.stderr)
    print("  It is unreachable but still holds this project's slot in the "
          "cloud. To remove it and re-provision under a servable name:",
          file=sys.stderr)
    print(f"    snodo serve --tunnel --delete --hostname {host}", file=sys.stderr)
    print("    snodo serve --tunnel", file=sys.stderr)
    return True


def _check_cloudflared() -> bool:
    """Return True if cloudflared is on PATH."""
    try:
        subprocess.run(
            ["which", "cloudflared"], capture_output=True, text=True, timeout=5,  # noqa: S607 - bare command name resolved from PATH by design; argv list, no shell
        )
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def _get_cloud_tunnel_api_url() -> str:
    """Read the tunnel worker's base URL from ~/.snodo/config.yml.

    Distinct from ``cloud.api_url`` (the audit ingest base): the tunnel
    worker is a separate worker on a separate host, so tunnel requests
    must never inherit the ingest base. Configs predating the split
    resolve to the default tunnel host.
    """
    from snodo.config import ConfigManager, get_cloud_tunnel_url

    return get_cloud_tunnel_url(ConfigManager().load())


def _get_snodo_api_key() -> str:
    """Read the snodo API key from ~/.snodo/config.yml cloud section."""
    from snodo.config import ConfigManager

    config = ConfigManager().load()
    return config.get("cloud", {}).get("api_key", "")


def _generate_short_id() -> str:
    """Generate a 6-character random alphanumeric short_id."""
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choices(chars, k=6))  # noqa: S311 - non-secret tunnel short_id (a collision-resistance convenience, not a credential); no cryptographic strength needed


# A single DNS label: 1-63 chars of [a-z0-9-], starting and ending with an
# alphanumeric. A hostname before the tunnel domain must match this exactly,
# or a wildcard certificate cannot cover it (issue #308).
_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")

_DNS_LABEL_MAX = 63
_SLUG_DIGEST_LEN = 6


def _tunnel_project_slug(project_root: str) -> str:
    """Reduce a project directory name to a single valid DNS label.

    The tunnel slug becomes the leading label of
    "<slug>-<mode>-<short>.tunnel.snodo.dev". A dot in that label is a label
    separator, not a character: a directory named "droptrack.io" would
    otherwise ask the cloud to serve a name one level deeper than the
    wildcard certificate covers, and every TLS client would be refused
    forever (issue #308).

    A name that is already a bare label is returned unchanged so existing
    working tunnels keep their hostnames. Anything else is sanitised —
    invalid characters folded to hyphens — and given a short deterministic
    digest of the original name, so two distinct directories ("droptrack.io"
    and "droptrack-io") can never collapse onto one hostname. Modes of one
    project still differ because the cloud appends the mode as a separate
    segment of the label.

    Raises TunnelAPIError for a name with nothing servable left after
    sanitising: rather than provision a tunnel that can never work, the
    operator is told why the name was refused.
    """
    raw = Path(project_root).name
    if _DNS_LABEL_RE.match(raw):
        return raw

    sanitised = re.sub(r"[^a-z0-9-]", "-", raw.lower())
    sanitised = re.sub(r"-{2,}", "-", sanitised).strip("-")
    if not sanitised or not re.search(r"[a-z0-9]", sanitised):
        raise TunnelAPIError(
            f"Project directory name {raw!r} cannot be turned into a tunnel "
            f"hostname: it has no DNS label characters (a-z, 0-9) to build "
            "from. Tunnel hostnames must be a single DNS label. Rename the "
            "project directory to something using letters or digits, or pass "
            "a different --protocol path inside such a directory."
        )

    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:_SLUG_DIGEST_LEN]
    budget = _DNS_LABEL_MAX - len(digest) - 1
    stem = sanitised[:budget].rstrip("-")
    return f"{stem}-{digest}"


def _provision_tunnel(
    api_key: str, project_slug: str, mode: str, short_id: str, snodo_version: str,
    port: int = DEFAULT_PORT, auth: Optional[list[str]] = None,
) -> dict:
    """Provision a tunnel via the snodo-cloud API.

    Returns a dict with hostname, tunnel_token.
    Raises TunnelAPIError on failure (409 carries existing_hostname).
    """
    try:
        import httpx

        api_url = _get_cloud_tunnel_api_url()

        url = f"{api_url.rstrip('/')}/tunnel/provision"
        payload = {
            "project_slug": project_slug,
            "mode": mode,
            "short_id": short_id,
            "snodo_version": snodo_version,
            "port": port,
        }
        payload["auth"] = auth or ["oauth"]
        resp = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )
        if resp.status_code != 200:
            existing = (
                _extract_existing_hostname(resp.text)
                if resp.status_code == 409 else None
            )
            raise TunnelAPIError(
                f"Tunnel provisioning failed: POST {url} returned "
                f"HTTP {resp.status_code}: {resp.text[:500]}",
                status_code=resp.status_code,
                existing_hostname=existing,
            )
        return resp.json()
    except TunnelAPIError:
        raise
    except Exception as e:
        raise TunnelAPIError(f"Tunnel provisioning failed: {e}") from e


def _rotate_tunnel_token(api_key: str, hostname: str) -> dict:
    """Deprecated — token rotation is no longer supported (OAuth 2.1 only)."""
    raise RuntimeError("Token rotation is no longer supported (OAuth 2.1 only).")


def _deprovision_tunnel(api_key: str, hostname: str) -> bool:
    """DELETE /tunnel/{hostname} — deprovision the tunnel.

    Returns True on success (200 or 404).
    Raises RuntimeError on other errors.
    """
    try:
        import httpx

        api_url = _get_cloud_tunnel_api_url()
        url = f"{api_url.rstrip('/')}/tunnel/{hostname}"
        resp = httpx.delete(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )
        if resp.status_code in (200, 404):
            return resp.status_code == 200
        raise TunnelAPIError(
            f"Tunnel deprovision failed: DELETE {url} returned "
            f"HTTP {resp.status_code}: {resp.text[:500]}",
            status_code=resp.status_code,
        )
    except TunnelAPIError:
        raise
    except Exception as e:
        raise TunnelAPIError(f"Tunnel deprovision failed: {e}") from e


def _handle_tunnel_delete(project_root: str, tunnel_config: dict,
                          api_key: str, hostname: Optional[str] = None) -> int:
    """Deprovision and remove a tunnel.

    With *hostname*, addresses the tunnel the cloud holds by that name
    even when this project's local config never recorded it (provisioned
    out of band). Without it, falls back to the locally stored hostname.
    Deletion stays deliberate: the target is only ever a hostname the
    operator named or a tunnel this project recorded.
    """
    local_hostname = tunnel_config.get("hostname", "")
    target = (hostname or "").strip() or local_hostname
    if not target:
        print("No tunnel configured for this project.", file=sys.stderr)
        print("  If the cloud holds a tunnel this project's config does not",
              file=sys.stderr)
        print("  record (e.g. provisioned out of band), delete it by name:",
              file=sys.stderr)
        print("    snodo serve --tunnel --delete --hostname <hostname>",
              file=sys.stderr)
        print("  The hostname is reported when 'snodo serve --tunnel' hits a conflict.",
              file=sys.stderr)
        return 1

    try:
        was_found = _deprovision_tunnel(api_key, target)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Only clear the local record when it pointed at the tunnel we just
    # removed — an explicit --hostname may target a different tunnel than
    # the one tunnel.json remembers.
    if not hostname or target == local_hostname:
        _delete_tunnel_file(project_root)

    if was_found:
        print(f"Tunnel deprovisioned: {target}")
    else:
        print(f"Tunnel {target} not found remotely, cleaned up locally.")
    return 0


def _delete_tunnel_file(project_root: str) -> None:
    """Remove .snodo/tunnel.json if it exists."""
    path = Path(project_root) / ".snodo" / "tunnel.json"
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _deprovision_newly_provisioned_tunnel(project_root: str, api_key: str,
                                          tunnel_config: dict) -> None:
    """Tear down a tunnel this run just provisioned after a failed start.

    A start that fails must not leave a tunnel behind it (Fixes #309). Only the
    tunnel this invocation created is removed — an existing tunnel recorded in
    tunnel.json is someone else's and stays. The local record is cleared too, so
    the next attempt provisions afresh rather than running cloudflared against a
    token whose tunnel is gone. Deprovision failures are reported but not
    re-raised: the start already failed, and the operator still needs the
    original error, not a second one.
    """
    hostname = tunnel_config.get("hostname")
    _delete_tunnel_file(project_root)
    if not hostname:
        return
    try:
        _deprovision_tunnel(api_key, hostname)
        print(f"Rolled back tunnel: {hostname}", file=sys.stderr)
    except RuntimeError as e:
        print(f"Warning: could not roll back tunnel {hostname}: {e}",
              file=sys.stderr)


def _load_tunnel_config(project_root: str) -> dict:
    """Load .snodo/tunnel.json or return empty dict."""
    path = Path(project_root) / ".snodo" / "tunnel.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_tunnel_config(project_root: str, config: dict) -> None:
    """Write .snodo/tunnel.json."""
    path = Path(project_root) / ".snodo" / "tunnel.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    to_save = {
        "hostname": config.get("hostname", ""),
        "tunnel_token": config.get("tunnel_token", ""),
        "created_at": config.get("created_at", ""),
    }
    if config.get("port") is not None:
        to_save["port"] = config["port"]
    if config.get("auth"):
        to_save["auth"] = list(config["auth"])
    path.write_text(json.dumps(to_save, indent=2) + "\n")


def _port_holder_pid(port: int, host: str = "127.0.0.1") -> Optional[int]:
    """Return the PID listening on *host*:*port*, or None if it cannot be found.

    ``lsof`` is the portable answer on macOS and Linux (both ship it, or a
    package away). When it is absent — a minimal container, a Windows host —
    the fallback is ``psutil``, which is already a test-time dependency. The
    helper never raises: not being able to find the holder is a reason to say
    less, not to turn a bind failure into a second stack trace.
    """
    try:
        out = subprocess.run(  # noqa: S603 - argv list, no shell
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],  # noqa: S607 - bare name resolved from PATH by design
            capture_output=True, text=True, timeout=5,
        )
        first = next((line.strip() for line in out.stdout.splitlines() if line.strip()), "")
        if first.isdigit():
            return int(first)
    except (OSError, subprocess.SubprocessError):
        _logger.debug("lsof could not identify the holder of port %s", port, exc_info=True)
    try:
        import psutil

        for conn in psutil.net_connections(kind="inet"):
            if (
                conn.status == psutil.CONN_LISTEN
                and conn.laddr
                and conn.laddr.port == port
                and conn.pid is not None
            ):
                return conn.pid
    except Exception:  # noqa: BLE001 — an observer must never break the start path
        _logger.debug("psutil could not identify the holder of port %s", port, exc_info=True)
    return None


def _describe_process(pid: int) -> str:
    """Describe a process by command and elapsed runtime, best-effort.

    Names HOW LONG the holder has been there because that is the fact that
    distinguishes the child this server just orphaned from an unrelated
    service the operator forgot about.
    """
    try:
        out = subprocess.run(  # noqa: S603 - argv list, no shell
            ["ps", "-o", "etime=,command=", "-p", str(pid)],  # noqa: S607 - bare name resolved from PATH by design
            capture_output=True, text=True, timeout=5,
        )
        line = out.stdout.strip()
        if line:
            elapsed, _, command = line.partition(" ")
            command = command.strip() or "?"
            return f"pid {pid} ({command}) has held it for {elapsed}"
    except (OSError, subprocess.SubprocessError):
        pass
    return f"pid {pid} holds it"


def _port_in_use_explanation(port: int, host: str = "127.0.0.1") -> str:
    """A sentence, not a stack trace, for 'cannot bind this port'.

    Reports who holds the port and for how long. This is the common aftermath
    of a server that was killed without its child being reaped: the child (or
    something it spawned) still owns the port, and the next start must name it
    rather than surfacing ``[Errno 48] ... address already in use`` raw
    (Fixes #290).
    """
    pid = _port_holder_pid(port, host)
    if pid is not None:
        return (
            f"Port {port} is already in use: {_describe_process(pid)}. "
            "That process — often an MCP child left behind by a previous "
            "server — must be stopped before this one can bind. "
            f"Stop it with: kill {pid}"
        )
    return (
        f"Port {port} is already in use, but the holder could not be "
        "identified. Find it with: lsof -nP -iTCP:%d -sTCP:LISTEN" % port
    )


def _tunnel_port_mismatch_explanation(
    binding_port: int,
    expected_port: int,
    tunnel_hostname: Optional[str] = None,
) -> str:
    """A sentence, not a stack trace, for a server port mismatch with its tunnel.

    A managed tunnel delivers to the port it was provisioned against. Binding a
    different port leaves the tunnel routing to whatever else holds the old one
    — on a machine with several projects, typically another project's MCP server
    (Fixes #389).
    """
    target = f"configured tunnel ({tunnel_hostname})" if tunnel_hostname else "configured tunnel"
    return (
        f"Server cannot bind port {binding_port}: {target} expects port {expected_port}. "
        f"Starting on port {binding_port} would leave the tunnel delivering to the old port "
        f"{expected_port} (often another project's server). "
        f"Start with --port {expected_port} to match the tunnel, or deprovision it first "
        f"with 'snodo serve --tunnel --delete'."
    )


def _find_free_port(start: int = DEFAULT_PORT, attempts: int = _PORT_SCAN_ATTEMPTS) -> Optional[int]:
    """Return the first free port at or after *start*, or None if none is free.

    Read-only by design: each candidate is tested with ``_port_holder_pid``
    (lsof/psutil), never by binding. Binding a port to test it and releasing it
    before the real bind would race another starter and, worse, momentarily
    make this process the holder of the port it is checking — turning the check
    into the very stale holder it exists to report (Fixes #309).

    The scan walks upward from the historical default so the first server on a
    machine keeps the port operators already expect, while a second lands one
    above it. Returns None only when every candidate in the window is held, so
    a genuinely exhausted range fails loudly rather than inventing a port.
    """
    for candidate in range(start, start + attempts):
        if candidate > 65535:
            break
        if _port_holder_pid(candidate) is None:
            return candidate
    return None


def _choose_serve_port(explicit_port: Optional[int]) -> Optional[int]:
    """Decide the port a non-stdio server should bind, or None if it cannot.

    An explicit ``--port`` is a promise: it is returned unchanged when free,
    and when taken the holder is named exactly as before (Fixes #290) and
    ``None`` is returned so the caller refuses rather than silently moving it.
    With no port named, a free one is chosen and reported, so any server can
    start alongside any other (Fixes #309).
    """
    if explicit_port is not None:
        if _port_holder_pid(explicit_port) is not None:
            print(f"Error: {_port_in_use_explanation(explicit_port)}", file=sys.stderr)
            return None
        return explicit_port

    port = _find_free_port()
    if port is None:
        print(
            f"Error: no free port found in {DEFAULT_PORT}–"
            f"{DEFAULT_PORT + _PORT_SCAN_ATTEMPTS - 1}. "
            "Pass --port to name one.",
            file=sys.stderr,
        )
        return None
    print(f"No --port given; using free port {port}", file=sys.stderr)
    return port


def _terminate_process_group(proc, timeout: float = 5.0) -> None:
    """Stop *proc* and everything in its process group, then reap it.

    A server that spawns children owns them: terminating only the direct child
    leaves whatever IT started — an MCP listener holding the port — alive. The
    children are started in their own session (``start_new_session=True``), so
    the whole group is addressable with ``killpg`` and can be torn down in one
    act (Fixes #290). The group is signalled SIGTERM first and SIGKILL after
    the grace period; a process whose group cannot be signalled (already
    exited) is signalled directly instead.
    """
    if proc is None:
        return
    _signal_process_group(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    _signal_process_group(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _signal_process_group(pid: int, sig: int) -> None:
    """Signal *pid*'s whole process group, falling back to the process alone."""
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, OSError):
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, OSError):
            pass


def _wait_for_server_bind(mcp_process, sleep_fn=None) -> bool:
    """Give the MCP server 2s to bind, then report if it is alive.

    The real wait is needed in production (uvicorn needs a moment to bind the
    port before cloudflared fronts it), but it is real wall-clock time that a
    test cannot parallelise away. *sleep_fn* is resolved at call time so tests
    can inject a clock that advances instantly (e.g. by patching
    ``time.sleep``); it defaults to the real ``time.sleep``.
    """
    if sleep_fn is None:
        sleep_fn = time.sleep
    sleep_fn(2.0)
    return mcp_process.poll() is None


def _print_tunnel_conflict(err: TunnelAPIError) -> None:
    """Report a 409 provisioning conflict in a form the operator can act on.

    A conflict is neither reused nor silently replaced. The cloud returns
    only the hostname of the blocking tunnel — not the tunnel_token
    needed to run cloudflared against it — and implicitly replacing a
    tunnel someone may be using would make removal an accident instead
    of a deliberate act. So the CLI names the blocker and hands back the
    exact commands to replace it, keeping deletion explicit.
    """
    print("The cloud allows one tunnel per project and mode, and this "
          "project's slot is already taken.", file=sys.stderr)
    if err.existing_hostname:
        print(f"Blocking tunnel: {err.existing_hostname}", file=sys.stderr)
        print("To replace it — deliberate: if that tunnel is in use, its "
              "clients will drop — run:", file=sys.stderr)
        print(f"  snodo serve --tunnel --delete --hostname {err.existing_hostname}",
              file=sys.stderr)
        print("  snodo serve --tunnel", file=sys.stderr)
    else:
        print("The conflict response did not name the existing tunnel.",
              file=sys.stderr)


def _drain_stream(
    stream: Any,
    sink: Optional[collections.deque[str]] = None,
    on_line: Optional[Callable[[str], None]] = None,
) -> Optional[threading.Thread]:
    """Continuously drain a child pipe in a background daemon thread.

    A pipe with no reader holds a bounded OS buffer (tens of kilobytes). Once
    full, the child's write() blocks indefinitely. This drainer reads lines
    until EOF, optionally preserving recent lines in *sink* (a bounded deque)
    for diagnostics and invoking *on_line* for each line.
    """
    if stream is None:
        return None

    # In unit tests, subprocess.Popen is often mocked with MagicMock where
    # readline() returns repeatedly without blocking. Real child pipes are
    # always io.IOBase instances; only spawn background drain threads on real
    # IO streams.
    if not isinstance(stream, io.IOBase):
        return None

    def _reader() -> None:
        while True:
            try:
                line = stream.readline()
            except (ValueError, OSError):
                break
            except UnicodeDecodeError:
                continue
            if not line:
                break
            if sink is not None:
                sink.append(line)
            if on_line is not None:
                try:
                    on_line(line)
                except Exception:  # noqa: S110
                    pass
        try:
            stream.close()
        except OSError:
            pass

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    return t


def _captured_stderr(process: Any, tail: collections.deque[str]) -> str:
    """Return drained stderr, with a guarded read only as a test fallback."""
    stderr_output = "".join(tail)
    if not stderr_output and process.stderr:
        try:
            stderr_output = process.stderr.read()
        except (ValueError, OSError):
            stderr_output = ""
    return stderr_output


def _run_tunnel(args, protocol, protocol_path) -> int:
    """Start an MCP server behind a managed Cloudflare tunnel.

    Provisioning is done via snodo-cloud API.  cloudflared runs as a
    subprocess alongside the MCP server.  Ctrl+C stops both cleanly.
    """
    project_root = _derive_project_root(args.protocol)
    mode = getattr(args, "mode", None) or "all"
    transport = getattr(args, "transport", "streamable-http")
    rotate = getattr(args, "rotate", False)
    delete = getattr(args, "delete", False)
    delete_hostname = getattr(args, "hostname", None) or None
    requested_auth = getattr(args, "auth", None)

    # Prefer streamable-http for tunnels
    if transport == "stdio":
        transport = "streamable-http"

    # 1. Check cloudflared
    if not _check_cloudflared():
        print("Error: cloudflared is required for managed tunnels.", file=sys.stderr)
        print("  macOS:   brew install cloudflared", file=sys.stderr)
        print("  Linux:   See https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/", file=sys.stderr)
        print("  Windows: winget install Cloudflare.cloudflared", file=sys.stderr)
        return 1

    # 2. Check snodo account
    api_key = _get_snodo_api_key()
    if not api_key:
        print("snodo serve --tunnel requires a free snodo account.", file=sys.stderr)
        print("  Sign up at: https://app.snodo.dev", file=sys.stderr)
        print("  Then run: snodo cloud connect <api_key>", file=sys.stderr)
        return 1

    # 3. Load existing tunnel config
    tunnel_config = _load_tunnel_config(project_root)
    auth_methods = _auth_methods(requested_auth or tunnel_config.get("auth"))

    # --delete flow
    if delete:
        return _handle_tunnel_delete(project_root, tunnel_config, api_key,
                                     delete_hostname)

    # --rotate flow (no-op)
    if rotate:
        print("Token rotation is no longer supported (OAuth 2.1 only).")
        return 0

    # 4. Decide the port before anything is provisioned. The tunnel is told the
    # port it must target when it is provisioned, and the MCP child is then
    # told that same port, so what the tunnel routes to and what the server
    # listens on cannot drift apart (Fixes #309). An explicit --port stays the
    # operator's promise. An existing tunnel already has a port baked into its
    # ingress rule, so its recorded port is reused rather than a new one chosen
    # underneath it; a tunnel recorded before the port was stored keeps the
    # historical default its ingress was built with. Only a fresh tunnel with
    # no --port has a port chosen for it, and that choice is reported.
    has_existing_tunnel = bool(tunnel_config.get("tunnel_token"))
    raw_tunnel_port = tunnel_config.get("port")
    try:
        tunnel_port = int(raw_tunnel_port) if raw_tunnel_port is not None else None
    except (ValueError, TypeError):
        tunnel_port = None

    explicit_port = getattr(args, "port", None)
    if explicit_port is not None:
        requested_port = explicit_port
    elif tunnel_port is not None:
        requested_port = tunnel_port
    elif has_existing_tunnel:
        requested_port = DEFAULT_PORT
    else:
        requested_port = None
    port = _choose_serve_port(requested_port)
    if port is None:
        return 1
    if tunnel_port is not None and port != tunnel_port:
        tunnel_hostname = tunnel_config.get("hostname")
        print(
            f"Error: {_tunnel_port_mismatch_explanation(port, tunnel_port, tunnel_hostname)}",
            file=sys.stderr,
        )
        return 1
    if requested_port is not None:
        # _choose_serve_port already announced a freely-found port; an explicit
        # or stored one is named here, so the operator is always told what the
        # tunnel targets.
        print(f"Using port {port} for tunnel", file=sys.stderr)

    # First run: provision
    newly_provisioned = False
    provisioned = {}
    if not tunnel_config.get("tunnel_token"):
        from snodo.version import __version__
        short_id = _generate_short_id()

        try:
            project_slug = _tunnel_project_slug(project_root)
        except TunnelAPIError as e:
            # The directory name cannot be reduced to a servable single DNS
            # label; refuse rather than provision a tunnel whose certificate
            # could never match (issue #308).
            print(f"Error: {e}", file=sys.stderr)
            return 1

        if project_slug != Path(project_root).name.lower():
            # A dotted (or otherwise unsafe) name now provisions under a
            # sanitised slug, so any older tunnel published under the raw
            # name still exists on the cloud side but can never serve TLS.
            print("Note: this project's directory name is not a single DNS "
                  "label, so the tunnel is published under a sanitised slug.",
                  file=sys.stderr)
            print("  If an older tunnel was provisioned under the raw "
                  "directory name, it can never work (its name sits one level",
                  file=sys.stderr)
            print("  deeper than the wildcard certificate). Remove it by name:",
                  file=sys.stderr)
            print("    snodo serve --tunnel --delete --hostname <old-hostname>",
                  file=sys.stderr)

        try:
            provisioned = _provision_with_auth(
                api_key, project_slug, mode, short_id, __version__, port,
                auth_methods,
            )
        except TunnelAPIError as e:
            print(f"Error: {e}", file=sys.stderr)
            if e.status_code == 409:
                _print_tunnel_conflict(e)
            elif e.status_code in (401, 403):
                print("The tunnel worker rejected your snodo API key.", file=sys.stderr)
                print("Re-run: snodo cloud connect <api_key>", file=sys.stderr)
            else:
                print("This is not an authentication error — re-running "
                      "'snodo cloud connect' will not change it.", file=sys.stderr)
            return 1
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

        tunnel_config = {
            "hostname": provisioned["hostname"],
            "tunnel_token": provisioned["tunnel_token"],
            "created_at": provisioned.get("created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ")),
            "port": port,
            "auth": auth_methods,
        }
        _save_tunnel_config(project_root, tunnel_config)
        newly_provisioned = True

    # 5. Start MCP server subprocess
    mcp_cmd = [
        sys.executable, "-m", "snodo.cli.main", "serve",
        "--protocol", args.protocol,
        "--transport", transport,
        "--port", str(port),
    ]
    for method in auth_methods:
        mcp_cmd.extend(["--auth", method])
    if mode != "all":
        mcp_cmd.extend(["--mode", mode])

    # start_new_session puts the MCP child (and anything IT spawns) in its own
    # process group, so stopping the tunnel stops the whole tree: terminating
    # only the direct child left the MCP listener alive holding the port, and
    # the next start died on a raw EADDRINUSE (Fixes #290).
    mcp_process = subprocess.Popen(  # noqa: S603 - argv list (no shell); protocol path and mode are single argv elements, never interpreted
        mcp_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    mcp_stderr_tail: collections.deque[str] = collections.deque(maxlen=1000)
    _drain_stream(mcp_process.stderr, sink=mcp_stderr_tail)

    # Verify the server actually bound before anything is called active. The
    # URL used to be printed as live before the child was even spawned, so a
    # bind failure handed the operator a public address routing to nothing
    # (Fixes #309).
    if not _wait_for_server_bind(mcp_process):
        stderr_output = _captured_stderr(mcp_process, mcp_stderr_tail)
        print(f"Error: MCP server exited with code {mcp_process.returncode}.",
              file=sys.stderr)
        if stderr_output:
            print(stderr_output, file=sys.stderr)
        _terminate_process_group(mcp_process)
        if newly_provisioned:
            _deprovision_newly_provisioned_tunnel(project_root, api_key,
                                                  tunnel_config)
        return 1

    # Bind confirmed: only now is the tunnel announced as active, and only now
    # is its URL handed to the operator.
    _warn_if_unservable_hostname(tunnel_config["hostname"])
    if newly_provisioned:
        _print_first_run_info(tunnel_config["hostname"], auth_methods, provisioned)
    else:
        print(f"✓ Snodo MCP tunnel active: https://{tunnel_config['hostname']}/mcp")
        print(f"  Authentication: {', '.join(auth_methods)} (any accepted mechanism)")
        print()

    # 6. Start cloudflared
    cf_cmd = [
        "cloudflared", "tunnel", "run",
        "--token", tunnel_config["tunnel_token"],
    ]
    cf_process = subprocess.Popen(  # noqa: S603 - argv list (no shell); the tunnel token is one argv element to cloudflared, never shell-interpreted
        cf_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    # 7. Wait for cloudflared to connect
    cf_connected = threading.Event()
    cf_stderr_tail: collections.deque[str] = collections.deque(maxlen=1000)

    def _on_cf_line(line: str) -> None:
        if "Registered tunnel connection" in line:
            cf_connected.set()

    _drain_stream(cf_process.stderr, sink=cf_stderr_tail, on_line=_on_cf_line)

    connected = False
    deadline = time.time() + 30
    while time.time() < deadline:
        if cf_connected.is_set():
            connected = True
            break
        if cf_process.stderr and not isinstance(cf_process.stderr, io.IOBase):
            line = cf_process.stderr.readline()
            if "Registered tunnel connection" in line:
                connected = True
                break
        if cf_process.poll() is not None:
            break
        time.sleep(0.1)

    if not connected and cf_process.poll() is None:
        # Still running, just didn't see the connect line yet — proceed
        pass

    print("Press Ctrl+C to stop.")

    # 8. Wait for Ctrl+C
    stop_requested = False

    def _cleanup(*_):
        nonlocal stop_requested
        stop_requested = True
        print("\nStopping...")
        # Stop the whole process group of each child, so nothing either one
        # started outlives the server holding the port (Fixes #290).
        for proc in [cf_process, mcp_process]:
            try:
                _terminate_process_group(proc)
            except Exception as e:
                _logger.debug("Could not terminate process group: %s", e)

    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    try:
        while True:
            try:
                cf_process.wait(timeout=5)
                break  # cloudflared exited on its own — not a success, handled below
            except subprocess.TimeoutExpired:
                pass
            if mcp_process.poll() is not None:
                print("Error: MCP server exited unexpectedly.", file=sys.stderr)
                stderr_output = "".join(mcp_stderr_tail)
                if not stderr_output and mcp_process.stderr:
                    try:
                        stderr_output = mcp_process.stderr.read()
                    except (ValueError, OSError):
                        stderr_output = ""
                if stderr_output:
                    print(stderr_output, file=sys.stderr)
                _cleanup()
                return 1
    except KeyboardInterrupt:
        _cleanup()
        return 0

    if stop_requested:
        return 0

    # cloudflared exited on its own while the MCP child was still healthy. That
    # child was started with start_new_session so nothing else can reap it —
    # left alone here it outlives the tunnel, still holding the port, and the
    # next `snodo serve --tunnel` dies on a raw EADDRINUSE (Fixes #290, #334).
    print(f"Error: cloudflared exited unexpectedly (code {cf_process.returncode}).",
          file=sys.stderr)
    stderr_output = _captured_stderr(cf_process, cf_stderr_tail)
    if stderr_output:
        print(stderr_output, file=sys.stderr)
    _cleanup()
    return 1


def _print_first_run_info(hostname: str, auth_methods: Optional[list[str]] = None,
                          credentials: Optional[dict] = None) -> None:
    """Print the first-run tunnel configuration block."""
    print()
    print("✓ Snodo MCP tunnel active")
    print()
    auth_methods = _auth_methods(auth_methods)
    credentials = credentials or {}
    print("Configure your MCP client with any accepted authentication mechanism:")
    print()
    print(f"  URL:              https://{hostname}/mcp")
    if "oauth" in auth_methods:
        print("  OAuth auth server: https://mcp-auth.snodo.dev")
    if "service-token" in auth_methods:
        print(f"  CF-Access-Client-Id: {credentials.get('client_id', '(not returned)')}")
        print(f"  CF-Access-Client-Secret: {credentials.get('client_secret', '(not returned)')}")
        print("  Save the service-token secret; it cannot be retrieved again.")
    print()
    print()
