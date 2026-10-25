"""HTTP bind and transport security helpers for ``snodo serve``."""

import ipaddress
import os
import re
import json
from pathlib import Path
import subprocess

from mcp.server.transport_security import TransportSecuritySettings

_TUNNEL_HOSTNAME_RE = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.tunnel|"
    r"[a-z0-9](?:[a-z0-9-]{0,54}[a-z0-9])?-tunnel|"
    r"[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])?-ws)\.snodo\.dev\b"
)


def extract_existing_hostname(body: str) -> str | None:
    """Extract a tunnel hostname from a provisioning conflict response."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        data = None
    if isinstance(data, dict) and isinstance(data.get("hostname"), str) and data["hostname"].strip():
        return data["hostname"].strip()
    match = _TUNNEL_HOSTNAME_RE.search(body)
    return match.group(0) if match else None


def check_cloudflared() -> bool:
    """Return whether cloudflared is available on PATH."""
    try:
        subprocess.run(  # noqa: S607 - command intentionally resolved from PATH
            ["which", "cloudflared"],  # noqa: S607 - resolved from PATH by design
            capture_output=True, text=True, timeout=5,
        )
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def derive_project_root(protocol_path: str) -> str:
    """Derive the project root from its protocol file path."""
    path = Path(protocol_path).resolve()
    return str(path.parent.parent if path.parent.name == ".snodo" else path.parent)


def is_loopback_host(host: str) -> bool:
    """Return whether a bind host resolves syntactically to loopback."""
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def build_transport_security_settings(
    port: int, tunnel_hostname: str | None
) -> TransportSecuritySettings:
    """Build HTTP Host protection settings for the bind and optional tunnel."""
    allowed_hosts = [f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"]
    allowed_origins = [
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
        f"http://[::1]:{port}",
    ]
    if tunnel_hostname:
        allowed_hosts.extend((tunnel_hostname, f"{tunnel_hostname}:{port}"))
        allowed_origins.append(f"https://{tunnel_hostname}")
    return TransportSecuritySettings(
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
        enable_dns_rebinding_protection=True,
    )


def configure_http_transport(mcp, port: int, tunnel_hostname: str | None) -> None:
    """Enable Host protection and trust forwarded headers only from local proxies."""
    os.environ["FORWARDED_ALLOW_IPS"] = "127.0.0.1,::1" if tunnel_hostname else ""
    allowed_hosts = [f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"]
    allowed_origins = [
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
        f"http://[::1]:{port}",
    ]
    if tunnel_hostname:
        allowed_hosts.extend((tunnel_hostname, f"{tunnel_hostname}:{port}"))
        allowed_origins.append(f"https://{tunnel_hostname}")
    mcp.settings.transport_security.allowed_hosts = allowed_hosts
    mcp.settings.transport_security.allowed_origins = allowed_origins
    mcp.settings.transport_security.enable_dns_rebinding_protection = True
