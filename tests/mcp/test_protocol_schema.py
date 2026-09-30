"""MCP protocol schema publication contract."""

import json
import subprocess

from typer.testing import CliRunner

from snodo.cli.commands.protocol_cmd import app
from snodo.compiler.models import Protocol
from snodo.mcp.server import ProtocolMCPServer


def _protocol(mode_id: str) -> Protocol:
    return Protocol(
        protocol_id="schema-test",
        name="Schema test",
        modes=[{"mode_id": mode_id, "name": mode_id, "tools": [], "validators": ["security"]}],
        validators=[{"validator_id": "security", "validator_type": "security", "criteria": ["check"]}],
        initial_mode=mode_id,
    )


def test_protocol_schema_is_exposed_in_every_mode_and_matches_cli(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    cli = json.loads(CliRunner().invoke(app, ["schema", "--json"]).stdout)
    for mode_id in ("producer", "empty"):
        server = ProtocolMCPServer(_protocol(mode_id), project_root=str(tmp_path))
        assert "protocol_schema" in {tool["name"] for tool in server.get_tools()}
        assert server.call_tool("protocol_schema", {}) == cli
