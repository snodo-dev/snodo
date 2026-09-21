"""The tunnel's recorded configuration and the credentials it holds.

FILE: snodo/cli/commands/serve_tunnel_config.py

Split out of ``serve_cmd.py``: reading and writing ``.snodo/tunnel.json``,
validating the requested authentication set, and deciding which recorded
credential a rotation means. None of it starts a server or calls the cloud
API — it is what the command knows about a tunnel before it acts.
"""

import json
import sys
from pathlib import Path
from typing import Optional

#: The authentication mechanisms a tunnel may accept. Several may be given;
#: a tunnel accepts ANY of the mechanisms it was provisioned with.
AUTH_METHODS = ("oauth", "service-token")


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


def _rotatable_tunnel_credentials(config: dict) -> list[dict]:
    """Return named credentials that the tunnel can actually rotate.

    OAuth bearer credentials are deliberately absent: their authorization
    server expires them and there is no replacement operation here. The
    ``credentials`` form is the cloud API's multi-credential representation;
    the client_id form keeps existing service-token tunnel records usable.
    """
    credentials = config.get("credentials")
    if isinstance(credentials, dict):
        credentials = list(credentials.values())
    if isinstance(credentials, list):
        return [
            item for item in credentials
            if isinstance(item, dict)
            and item.get("rotatable", True)
            and str(item.get("type", "")).lower() not in {"oauth", "bearer", "oauth2"}
        ]
    if config.get("client_id") and str(config.get("auth_type", "")).lower() not in {
        "oauth", "bearer", "oauth2",
    }:
        return [{"name": "service token", "type": "service_token"}]
    return []


def _select_tunnel_credential(config: dict, requested: Optional[str]) -> Optional[dict]:
    """Select one rotatable credential, or report why selection is impossible."""
    credentials = _rotatable_tunnel_credentials(config)
    if requested:
        selected = next(
            (item for item in credentials
             if requested in {item.get("name"), item.get("id"), item.get("type")}),
            None,
        )
        if selected is None:
            print(f"No rotatable tunnel credential named '{requested}'.", file=sys.stderr)
        return selected
    if len(credentials) == 1:
        return credentials[0]
    if not credentials:
        print("This tunnel has no rotatable credentials. OAuth bearer tokens "
              "expire through the authorization server and cannot be rotated.",
              file=sys.stderr)
        return None
    names = ", ".join(str(item.get("name") or item.get("id") or item.get("type"))
                     for item in credentials)
    print(f"This tunnel has more than one rotatable credential: {names}.", file=sys.stderr)
    print("Choose one with --credential <name>.", file=sys.stderr)
    return None


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
    if config.get("client_id"):
        to_save["client_id"] = config["client_id"]
    if config.get("credentials"):
        to_save["credentials"] = config["credentials"]
    # A tunnel provisioned before the auth set was recorded carries a single
    # auth_type; it is read when deciding what is rotatable.
    if config.get("auth_type"):
        to_save["auth_type"] = config["auth_type"]
    path.write_text(json.dumps(to_save, indent=2) + "\n")
