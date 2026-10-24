"""Wire-level guards for the MCP 2.x server handshake and tool surface."""

import anyio
import subprocess
from mcp.client.session import ClientSession
from mcp.server import InitializationOptions
from mcp.shared.memory import create_client_server_memory_streams
from snodo.compiler.models import Protocol
from snodo.mcp.server import ProtocolMCPServer
from snodo.mcp.tools import TOOL_REGISTRY
from snodo.mcp.transport import build_mcp_server


_PROTOCOL_DATA = {
    "protocol_id": "handshake-test",
    "name": "Handshake test",
    "version": "1.0.0",
    "modes": [
        {"mode_id": "producer", "name": "Producer", "tools": ["edit", "test"]},
        {"mode_id": "reviewer", "name": "Reviewer", "tools": ["review"]},
    ],
    "validators": [{"validator_id": "security", "validator_type": "security", "criteria": ["Check security"]}],
    "disagreement_policy": "unanimous",
    "initial_mode": "producer",
}


def _protocol_server(tmp_path, mode_id=None):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    return ProtocolMCPServer(Protocol(**_PROTOCOL_DATA), str(tmp_path), mode_id=mode_id)


async def _listed_tools(server):
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        names = set()
        async with anyio.create_task_group() as tasks:
            async def serve():
                await server._lowlevel_server.run(
                    *server_streams,
                    InitializationOptions(
                        server_name=server.name,
                        server_version="test",
                        capabilities=server._lowlevel_server.get_capabilities(),
                        instructions=server.instructions,
                    ),
                )

            tasks.start_soon(serve)
            async with ClientSession(*client_streams) as client:
                await client.initialize()
                result = await client.list_tools()
                tasks.cancel_scope.cancel()
                names.update(tool.name for tool in result.tools)
                tasks.cancel_scope.cancel()
        return names


def test_mcp_handshake_lists_protocol_tool_surface(tmp_path):
    protocol_server = _protocol_server(tmp_path)
    mcp_server = build_mcp_server(protocol_server)

    names = anyio.run(_listed_tools, mcp_server)

    assert names == {tool["name"] for tool in protocol_server.get_tools()} | {"guide"}
    assert names <= set(TOOL_REGISTRY) | {"guide"}


def test_mcp_handshake_filters_restricted_mode_tools(tmp_path):
    protocol_server = _protocol_server(tmp_path, mode_id="reviewer")
    mcp_server = build_mcp_server(protocol_server)

    names = anyio.run(_listed_tools, mcp_server)

    expected = {tool["name"] for tool in protocol_server.get_tools()} | {"guide"}
    assert names == expected
    assert "read_diff" in names
    assert "run_tests" not in names
