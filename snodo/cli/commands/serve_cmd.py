"""Serve command - Start MCP server from protocol definition.

FILE: snodo/cli/commands/serve_cmd.py
"""

import json
import os
import random
import signal
import string
import subprocess
import sys
import time
from pathlib import Path

from snodo.cli.commands import load_protocol


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
    protocol_path = Path(args.protocol)
    protocol = load_protocol(protocol_path)
    if not protocol:
        return 1

    if getattr(args, "tunnel", False):
        return _run_tunnel(args, protocol, protocol_path)

    if args.install:
        print("Note: 'serve --install' is deprecated. Use 'snodo install' instead.",
              file=sys.stderr)
        return _handle_install(args, protocol, protocol_path)

    if getattr(args, "uninstall_all", False):
        print("Note: 'serve --uninstall-all' is deprecated. Use 'snodo uninstall --all' instead.",
              file=sys.stderr)
        return _handle_uninstall_all()

    if args.uninstall:
        print("Note: 'serve --uninstall' is deprecated. Use 'snodo uninstall' instead.",
              file=sys.stderr)
        return _handle_uninstall(args, protocol, protocol_path)

    return _run_server(args, protocol)


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

    mcp = build_fastmcp_server(protocol_server)
    tools = protocol_server.get_tools()
    mode_label = mode_id or "all"

    port = getattr(args, "port", 55441)
    if port is not None:
        mcp.settings.port = port

    # Accept proxied requests when not using stdio
    if transport != "stdio":
        os.environ["FORWARDED_ALLOW_IPS"] = "*"

    print(
        f"Snodo MCP [{protocol.protocol_id}] mode={mode_label} "
        f"tools={len(tools)} transport={transport}",
        file=sys.stderr,
    )

    if transport != "stdio":
        print()
        print("To expose this server remotely, use your own tunneling tool:")
        print(f"  ngrok:        ngrok http {port}")
        print(f"  cloudflared:  cloudflared tunnel --url http://localhost:{port}")
        print(f"  tailscale:    tailscale funnel {port}")
        print()
        print("  Or use: snodo serve --tunnel (requires free snodo account)")

    mcp.run(transport=transport)
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


def _check_cloudflared() -> bool:
    """Return True if cloudflared is on PATH."""
    try:
        subprocess.run(
            ["which", "cloudflared"], capture_output=True, text=True, timeout=5,
        )
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def _get_cloud_api_url() -> str:
    """Read the cloud API URL from ~/.snodo/config.yml."""
    from snodo.cli.config import ConfigManager

    config = ConfigManager().load()
    return config.get("cloud", {}).get("api_url", "https://api.snodo.dev")


def _get_snodo_api_key() -> str:
    """Read the snodo API key from ~/.snodo/config.yml cloud section."""
    from snodo.cli.config import ConfigManager

    config = ConfigManager().load()
    return config.get("cloud", {}).get("api_key", "")


def _generate_short_id() -> str:
    """Generate a 6-character random alphanumeric short_id."""
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choices(chars, k=6))


def _provision_tunnel(
    api_key: str, project_slug: str, mode: str, short_id: str, snodo_version: str, port: int = 55441,
) -> dict:
    """Provision a tunnel via the snodo-cloud API.

    Returns a dict with hostname, tunnel_token, client_id, client_secret.
    Raises RuntimeError on failure.
    """
    try:
        import httpx

        api_url = _get_cloud_api_url()

        url = f"{api_url.rstrip('/')}/tunnel/provision"
        payload = {
            "project_slug": project_slug,
            "mode": mode,
            "short_id": short_id,
            "snodo_version": snodo_version,
            "port": port,
        }
        resp = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Tunnel provisioning failed (HTTP {resp.status_code}): {resp.text[:500]}"
            )
        return resp.json()
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Tunnel provisioning failed: {e}")


def _rotate_tunnel_token(api_key: str, hostname: str) -> dict:
    """Rotate the service token for an existing tunnel.

    Returns a dict with client_id and client_secret.
    """
    try:
        import httpx

        api_url = _get_cloud_api_url()

        url = f"{api_url.rstrip('/')}/tunnel/{hostname}/token"
        resp = httpx.post(
            url,
            json={},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Token rotation failed (HTTP {resp.status_code}): {resp.text[:500]}"
            )
        return resp.json()
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Token rotation failed: {e}")


def _deprovision_tunnel(api_key: str, hostname: str) -> bool:
    """DELETE /tunnel/{hostname} — deprovision the tunnel.

    Returns True on success (200 or 404).
    Raises RuntimeError on other errors.
    """
    try:
        import httpx

        api_url = _get_cloud_api_url()
        url = f"{api_url.rstrip('/')}/tunnel/{hostname}"
        resp = httpx.delete(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )
        if resp.status_code in (200, 404):
            return resp.status_code == 200
        raise RuntimeError(
            f"Tunnel deprovision failed (HTTP {resp.status_code}): {resp.text[:500]}"
        )
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Tunnel deprovision failed: {e}")


def _handle_tunnel_delete(project_root: str, tunnel_config: dict,
                          api_key: str) -> int:
    """Deprovision and remove the tunnel."""
    hostname = tunnel_config.get("hostname", "")
    if not hostname:
        print("No tunnel configured for this project.")
        return 1

    try:
        was_found = _deprovision_tunnel(api_key, hostname)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    _delete_tunnel_file(project_root)

    if was_found:
        print("Tunnel deprovisioned.")
    else:
        print("Tunnel not found remotely, cleaned up locally.")
    return 0


def _delete_tunnel_file(project_root: str) -> None:
    """Remove .snodo/tunnel.json if it exists."""
    path = Path(project_root) / ".snodo" / "tunnel.json"
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


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
    """Write .snodo/tunnel.json (never includes client_secret)."""
    path = Path(project_root) / ".snodo" / "tunnel.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    to_save = {
        "hostname": config.get("hostname", ""),
        "tunnel_token": config.get("tunnel_token", ""),
        "client_id": config.get("client_id", ""),
        "created_at": config.get("created_at", ""),
    }
    path.write_text(json.dumps(to_save, indent=2) + "\n")


def _run_tunnel(args, protocol, protocol_path) -> int:
    """Start an MCP server behind a managed Cloudflare tunnel.

    Provisioning is done via snodo-cloud API.  cloudflared runs as a
    subprocess alongside the MCP server.  Ctrl+C stops both cleanly.
    """
    project_root = _derive_project_root(args.protocol)
    project_slug = Path(project_root).name
    mode = getattr(args, "mode", None) or "all"
    transport = getattr(args, "transport", "streamable-http")
    port = getattr(args, "port", 55441)
    rotate = getattr(args, "rotate", False)
    delete = getattr(args, "delete", False)

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

    # --delete flow
    if delete:
        return _handle_tunnel_delete(project_root, tunnel_config, api_key)

    # --rotate flow
    if rotate:
        if not tunnel_config.get("hostname"):
            print("Error: No existing tunnel to rotate. Run without --rotate first.", file=sys.stderr)
            return 1
        try:
            new_token = _rotate_tunnel_token(api_key, tunnel_config["hostname"])
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        tunnel_config["client_id"] = new_token["client_id"]
        _save_tunnel_config(project_root, tunnel_config)
        _print_first_run_info(tunnel_config["hostname"], new_token["client_id"], new_token["client_secret"])
        print()
        print("Token rotated successfully.")
        return 0

    # First run: provision
    if not tunnel_config.get("tunnel_token"):
        from snodo import __version__
        short_id = _generate_short_id()

        try:
            provisioned = _provision_tunnel(
                api_key, project_slug, mode, short_id, __version__, port,
            )
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            print("If you see an authentication error, your snodo API key may have expired.", file=sys.stderr)
            print("Re-run: snodo cloud connect <api_key>", file=sys.stderr)
            return 1

        tunnel_config = {
            "hostname": provisioned["hostname"],
            "tunnel_token": provisioned["tunnel_token"],
            "client_id": provisioned["client_id"],
            "created_at": provisioned.get("created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ")),
        }
        _save_tunnel_config(project_root, tunnel_config)

        _print_first_run_info(
            provisioned["hostname"],
            provisioned["client_id"],
            provisioned["client_secret"],
        )
    else:
        # Subsequent runs
        print(f"✓ Snodo MCP tunnel active: https://{tunnel_config['hostname']}/mcp")
        print("  (Use your saved CF-Access-Client-Id and CF-Access-Client-Secret)")
        print()

    # 4. Start MCP server subprocess
    mcp_cmd = [
        sys.executable, "-m", "snodo.cli.main", "serve",
        "--protocol", args.protocol,
        "--transport", transport,
        "--port", str(port),
    ]
    if mode != "all":
        mcp_cmd.extend(["--mode", mode])

    mcp_process = subprocess.Popen(
        mcp_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Verify MCP server started — give uvicorn 2s to bind, then check process alive
    time.sleep(2)
    if mcp_process.poll() is not None:
        stderr_output = mcp_process.stderr.read() if mcp_process.stderr else ""
        print(f"Error: MCP server exited with code {mcp_process.returncode}.",
              file=sys.stderr)
        if stderr_output:
            print(stderr_output, file=sys.stderr)
        return 1

    # 5. Start cloudflared
    cf_cmd = [
        "cloudflared", "tunnel", "run",
        "--token", tunnel_config["tunnel_token"],
    ]
    cf_process = subprocess.Popen(
        cf_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # 6. Wait for cloudflared to connect
    connected = False
    deadline = time.time() + 30
    while time.time() < deadline:
        line = cf_process.stderr.readline() if cf_process.stderr else ""
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

    # 7. Wait for Ctrl+C
    def _cleanup(*_):
        print("\nStopping...")
        for proc in [cf_process, mcp_process]:
            try:
                proc.terminate()
            except Exception:
                pass
        for proc in [cf_process, mcp_process]:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass

    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    try:
        while True:
            try:
                cf_process.wait(timeout=5)
                break  # cloudflared exited normally
            except subprocess.TimeoutExpired:
                pass
            if mcp_process.poll() is not None:
                print("Error: MCP server exited unexpectedly.", file=sys.stderr)
                stderr_output = mcp_process.stderr.read() if mcp_process.stderr else ""
                if stderr_output:
                    print(stderr_output, file=sys.stderr)
                _cleanup()
                return 1
    except KeyboardInterrupt:
        _cleanup()

    return 0


def _print_first_run_info(hostname: str, client_id: str, client_secret: str) -> None:
    """Print the first-run tunnel configuration block."""
    print()
    print("✓ Snodo MCP tunnel active")
    print()
    print("Configure in your AI provider (Claude, Gemini, ChatGPT, etc.):")
    print()
    print(f"  URL:    https://{hostname}/mcp")
    print(f"  Header: CF-Access-Client-Id: {client_id}")
    print(f"  Header: CF-Access-Client-Secret: {client_secret}")
    print()
    print("⚠  Save the Client Secret — it will not be shown again.")
    print("   Rotate with: snodo serve --tunnel --rotate")
    print()
