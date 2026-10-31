"""Tests for recording and reading intents over the MCP tool surface."""

import subprocess

import pytest

from snodo.compiler.models import Protocol
from snodo.mcp.server import MCPError, ProtocolMCPServer


@pytest.fixture
def project_dir(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("test")
    (tmp_path / ".snodo").mkdir()
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True, check=True)
    return str(tmp_path)


def _server(root, mode="planner"):
    data = {
        "protocol_id": "intent_test", "name": "Intent test", "version": "1.0.0",
        "initial_mode": "planner",
        "modes": [
            {"mode_id": "planner", "name": "Planner", "tools": ["plan"], "validators": ["v"]},
            {"mode_id": "reader", "name": "Reader", "tools": ["edit"], "validators": ["v"]},
        ],
        "validators": [{"validator_id": "v", "validator_type": "quality", "criteria": ["ok"]}],
        "disagreement_policy": "unanimous",
    }
    return ProtocolMCPServer(Protocol(**data), root, mode_id=mode)


def test_record_get_list_intents(project_dir):
    server = _server(project_dir)
    first = server.call_tool("record_intent", {"text": "First", "issue": "#1"})
    second = server.call_tool("record_intent", {"text": "Second", "supersedes": first["id"]})
    assert server.call_tool("get_intent", {"intent_id": first["id"]})["text"] == "First"
    records = server.call_tool("list_intents", {})
    assert [item["id"] for item in records] == [second["id"], first["id"]]
    assert server.call_tool("list_intents", {"limit": 1}) == [second]


@pytest.mark.parametrize(("tool", "args", "message"), [
    ("record_intent", {"text": "  "}, "non-empty"),
    ("record_intent", {"text": "x" * 10001}, "exceeds"),
    ("record_intent", {"text": "x", "supersedes": "int_ffffff"}, "not found"),
    ("get_intent", {"intent_id": "int_ffffff"}, "not found"),
])
def test_intent_errors_are_clear(project_dir, tool, args, message):
    with pytest.raises(MCPError, match=message):
        _server(project_dir).call_tool(tool, args)


def test_intent_tools_are_planning_mode_only(project_dir):
    planner = _server(project_dir)
    reader = _server(project_dir, "reader")
    assert {"record_intent", "get_intent", "list_intents"} <= {t["name"] for t in planner.get_tools()}
    assert not ({"record_intent", "get_intent", "list_intents"} & {t["name"] for t in reader.get_tools()})
    with pytest.raises(MCPError, match="Unknown tool: record_intent"):
        reader.call_tool("record_intent", {"text": "not allowed"})
