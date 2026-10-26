"""OAuth and host wiring for managed tunnel MCP serves."""

import sys

from snodo.cli.commands.serve_security import _TUNNEL_HOSTNAME_RE
from snodo.cli.commands.serve_tunnel_config import _load_tunnel_config, _tunnel_transport


def configure_tunnel_auth(project_root, load_tunnel_config=_load_tunnel_config):
    """Build tunnel-specific verifier, auth settings, and host configuration."""
    tunnel_config = load_tunnel_config(project_root)
    tunnel_hostname = tunnel_config.get("hostname")
    ws_tunnel = _tunnel_transport(tunnel_config.get("transport")) == "ws"
    ws_public_hostname = tunnel_config.get("public_hostname") if ws_tunnel else None
    ws_org_id = tunnel_config.get("org_id") if ws_tunnel else None
    ws_hosts = []
    if ws_tunnel:
        for configured_host in (tunnel_hostname, ws_public_hostname):
            if configured_host and _TUNNEL_HOSTNAME_RE.fullmatch(configured_host):
                ws_hosts.append(configured_host)
            elif configured_host:
                print(
                    "Ignoring invalid tunnel hostname; run `snodo serve --tunnel --public-url` to refresh the config.",
                    file=sys.stderr,
                )
        if ws_public_hostname and not ws_org_id:
            print(
                "Ignoring public hostname without an organization id; run `snodo serve --tunnel --public-url` to refresh the config.",
                file=sys.stderr,
            )
            ws_hosts = [host for host in ws_hosts if host != ws_public_hostname]
    extra_kwargs = {}
    tunnel_auth = (
        ["oauth"] if tunnel_hostname and _tunnel_transport(tunnel_config.get("transport")) == "ws"
        else tunnel_config.get("auth", ["oauth"]) if tunnel_hostname else []
    )
    oauth_enabled = "oauth" in tunnel_auth
    access_verifier = None
    if tunnel_hostname and oauth_enabled:
        from snodo.infrastructure.jwks import JwksClient
        from snodo.infrastructure.oauth_verifier import JwksTokenVerifier
        from mcp.server.auth.settings import AuthSettings
        from pydantic import AnyHttpUrl

        jwks = JwksClient()
        if not jwks.fetch():
            print("  Error: OAuth 2.1 required for managed tunnels; could not load JWKS", file=sys.stderr)
            return 1
        resource = f"https://{tunnel_hostname}/mcp"
        if ws_tunnel:
            audiences = {host: f"https://{host}/mcp" for host in ws_hosts}
            extra_kwargs["token_verifier"] = JwksTokenVerifier(
                jwks, audiences=audiences, expected_org_id=ws_org_id
            )
        else:
            extra_kwargs["token_verifier"] = JwksTokenVerifier(jwks, resource=resource)
        auth_kwargs = {}
        if ws_tunnel:
            auth_kwargs["validate_token_resource"] = False
        extra_kwargs["auth_settings"] = AuthSettings(
            issuer_url=AnyHttpUrl("https://mcp-auth.snodo.dev"),
            resource_server_url=AnyHttpUrl(resource),
            **auth_kwargs,
        )
        print("  OAuth 2.1 enabled (RS256 JWTs from mcp-auth.snodo.dev)", file=sys.stderr)
    if tunnel_hostname and "service-token" in tunnel_auth:
        from snodo.infrastructure.access_assertion import AccessAssertionVerifier
        access_verifier = AccessAssertionVerifier(
            tunnel_config["access_team_domain"], tunnel_config["access_aud"]
        )

    return (tunnel_config, tunnel_hostname, ws_tunnel, ws_public_hostname, ws_hosts, tunnel_auth, oauth_enabled, access_verifier, extra_kwargs)
