"""FastMCP transport bridge for Snodo MCP server.

FILE: snodo/mcp/transport.py

Bridges ProtocolMCPServer (tool resolution, WF1 enforcement) to FastMCP
(official MCP SDK transport). Replaces the custom stdio/SSE transport
that didn't work with Claude Desktop.

ProtocolMCPServer handles:
- Protocol-driven tool resolution (MODE_TOOL_MAP -> TOOL_REGISTRY)
- WF1 enforcement (validation tokens for mutating tools)
- Dispatching to backing MCPs (workspace, git, shell, pr, planner)

FastMCP handles:
- MCP protocol handshake (initialize, notifications)
- JSON-RPC framing (Content-Length headers, stdio)
- Tool listing and calling via MCP protocol
"""

import inspect
import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from snodo.mcp.server import ProtocolMCPServer


_JSON_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def build_fastmcp_server(protocol_server: ProtocolMCPServer) -> FastMCP:
    """Build a FastMCP server that delegates to a ProtocolMCPServer.

    Creates a FastMCP instance with tools matching the protocol configuration.
    Each tool handler delegates to protocol_server.call_tool() which handles
    WF1 enforcement and dispatching to backing MCPs.

    Args:
        protocol_server: ProtocolMCPServer with resolved tools and WF1 state

    Returns:
        Configured FastMCP instance ready to run
    """
    server_name = f"snodo-{protocol_server.protocol.protocol_id}"
    if protocol_server.mode_id:
        server_name += f"-{protocol_server.mode_id}"

    mcp = FastMCP(server_name)

    for tool_info in protocol_server.get_tools():
        fn = _make_tool_handler(protocol_server, tool_info)
        mcp.add_tool(fn, name=tool_info["name"], description=tool_info["description"])

    return mcp


def _make_tool_handler(
    protocol_server: ProtocolMCPServer, tool_info: dict
) -> Any:
    """Create a tool handler function with proper signature for FastMCP.

    FastMCP inspects function signatures to generate input schemas.
    We build a function with the correct parameters matching our
    TOOL_REGISTRY schema so Claude sees proper parameter descriptions.

    Args:
        protocol_server: Server to delegate tool calls to
        tool_info: Tool descriptor with name, description, inputSchema

    Returns:
        A callable with proper __signature__ for FastMCP inspection
    """
    tool_name = tool_info["name"]
    schema = tool_info["inputSchema"]
    properties = schema.get("properties", {})
    required_set = set(schema.get("required", []))

    # Build inspect.Parameter list from JSON Schema
    params = []
    annotations = {}

    for pname, pinfo in properties.items():
        ptype = _JSON_TYPE_MAP.get(pinfo.get("type", "string"), str)
        annotations[pname] = ptype

        if pname in required_set:
            params.append(inspect.Parameter(
                pname, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=ptype,
            ))
        else:
            default = pinfo.get("default", None)
            params.append(inspect.Parameter(
                pname, inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default, annotation=ptype,
            ))

    annotations["return"] = str

    # Create handler closure that delegates to protocol server
    is_slow = protocol_server.is_slow_tool(tool_name)

    if is_slow:
        async def handler(**kwargs) -> str:
            result = await protocol_server.call_tool_async(tool_name, kwargs)
            if isinstance(result, str):
                return result
            return json.dumps(result, default=str)
    else:
        def handler(**kwargs) -> str:
            result = protocol_server.call_tool(tool_name, kwargs)
            if isinstance(result, str):
                return result
            return json.dumps(result, default=str)

    handler.__name__ = tool_name
    handler.__doc__ = tool_info["description"]
    handler.__signature__ = inspect.Signature(params, return_annotation=str)  # type: ignore[attr-defined]
    handler.__annotations__ = annotations

    return handler
