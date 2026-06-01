"""Serve command - Start MCP server from protocol definition.

FILE: snodo/cli/commands/serve_cmd.py
"""

import sys
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

    print(
        f"Snodo MCP [{protocol.protocol_id}] mode={mode_label} "
        f"tools={len(tools)} transport={args.transport}",
        file=sys.stderr,
    )

    mcp.run(transport=args.transport)
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
