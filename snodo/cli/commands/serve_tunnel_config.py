"""The tunnel's recorded configuration and the credentials it holds.

FILE: snodo/cli/commands/serve_tunnel_config.py

Split out of ``serve_cmd.py``: reading and writing ``.snodo/tunnel.json``,
validating the requested authentication set, and deciding which recorded
credential a rotation means. None of it starts a server or calls the cloud
API — it is what the command knows about a tunnel before it acts.
"""

import json
import logging
from pathlib import Path
from typing import Optional

#: Supported tunnel authentication selection.
AUTH_METHODS = ("oauth", "service-token")
TUNNEL_TRANSPORTS = ("cloudflare", "ws")
_logger = logging.getLogger(__name__)


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


def _tunnel_transport(value: Optional[str]) -> str:
    """Return the selected tunnel transport, defaulting to Cloudflare."""
    if value is None:
        return "cloudflare"
    if value not in TUNNEL_TRANSPORTS:
        raise ValueError(
            f"invalid tunnel transport {value!r}; choose cloudflare or ws"
        )
    return value


def _validate_tunnel_auth(transport: str, auth: Optional[list[str]]) -> list[str]:
    """Validate authentication choices for the selected tunnel transport."""
    selected = _auth_methods(auth)
    if transport == "ws" and "service-token" in selected:
        raise ValueError("service-token auth is not supported over ws yet")
    return selected


def _has_usable_saved_tunnel(config: dict) -> bool:
    """Whether saved tunnel state is sufficient to reuse the provisioned tunnel."""
    if _tunnel_transport(config.get("transport")) == "ws":
        return bool(config.get("hostname"))
    return bool(config.get("tunnel_token"))


def _load_tunnel_config(project_root: str) -> dict:
    """Load .snodo/tunnel.json or return empty dict."""
    path = Path(project_root) / ".snodo" / "tunnel.json"
    if not path.exists():
        return {}
    try:
        config = json.loads(path.read_text())
        if isinstance(config, dict):
            config.setdefault("transport", "cloudflare")
        return config
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
        "transport": _tunnel_transport(config.get("transport")),
    }
    # These are ws provisioning details. Keep them optional so older and
    # Cloudflare tunnel configurations retain their existing shape.
    for key in ("org_id", "public_hostname", "last_printed_public_hostname"):
        if key in config:
            to_save[key] = config[key]
    if "public_hostname" not in config:
        existing = _load_tunnel_config(project_root)
        if "public_hostname" in existing:
            to_save["public_hostname"] = existing["public_hostname"]
    if config.get("machine_id") is not None:
        to_save["machine_id"] = config["machine_id"]
    if config.get("port") is not None:
        to_save["port"] = config["port"]
    if config.get("auth") is not None:
        to_save["auth"] = config["auth"]
    for key in ("client_id", "access_path", "access_team_domain", "access_aud"):
        if config.get(key) is not None:
            to_save[key] = config[key]
    # Recovery metadata is local lifecycle state, not a protocol/job status.
    # Never persist client_secret.
    recovery = config.get("recovery")
    if isinstance(recovery, dict) and recovery.get("action") == "delete":
        to_save["recovery"] = {
            "action": "delete",
            "hostname": str(recovery.get("hostname", "")),
        }
    path.write_text(json.dumps(to_save, indent=2) + "\n")


def resolve_serve_mode(
    project_root: str, protocol, requested_mode: Optional[str] = None,
) -> str:
    """Resolve an explicit serve mode or the project's current/default mode."""
    if requested_mode:
        return requested_mode
    try:
        from snodo.infrastructure.state import read_state

        current = read_state(project_root).current_mode
        if current and protocol.get_mode(current):
            return current
    except Exception as e:  # noqa: BLE001 — state is best-effort
        _logger.debug("Could not read current mode for tunnel: %s", e)
    return protocol.initial_mode
