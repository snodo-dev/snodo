"""The protocol-gated MCP write surface is confined, and audited."""

import hashlib
import subprocess

import pytest
from snodo.compiler.models import Protocol
from snodo.infrastructure.audit import AuditLog
from snodo.mcp.server import MCPError, ProtocolMCPServer


def _protocol(*, tools, prefixes=None):
    data = {
        "protocol_id": "write_test",
        "name": "Write test",
        "modes": [{"mode_id": "author", "name": "Author", "tools": tools}],
        "validators": [{"validator_id": "v", "validator_type": "security", "criteria": ["check"]}],
        "initial_mode": "author",
    }
    if prefixes is not None:
        data["write_allowed_prefixes"] = prefixes
    return Protocol(**data)


@pytest.fixture
def project(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / ".snodo").mkdir()
    (tmp_path / ".gitignore").write_text(".snodo/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_write_tool_is_exposed_only_when_mode_grants_write(project):
    with_write = ProtocolMCPServer(_protocol(tools=["write"]), str(project), mode_id="author")
    without_write = ProtocolMCPServer(_protocol(tools=["edit"]), str(project), mode_id="author")

    assert "write_file" in {tool["name"] for tool in with_write.get_tools()}
    assert "write_file" not in {tool["name"] for tool in without_write.get_tools()}
    assert "delete_file" not in {tool["name"] for tool in with_write.get_tools()}
    with pytest.raises(MCPError, match="Unknown tool"):
        without_write.call_tool("write_file", {"path": ".snodo/x", "content": "x"})


def test_default_write_is_confined_to_snodo_and_does_not_commit(project):
    server = ProtocolMCPServer(_protocol(tools=["write"]), str(project), mode_id="author")
    result = server.call_tool("write_file", {"path": ".snodo/plans/p.yml", "content": "plan ✓"})

    assert result == {"path": ".snodo/plans/p.yml", "bytes": len("plan ✓".encode())}
    assert (project / result["path"]).read_text() == "plan ✓"
    assert subprocess.run(["git", "status", "--porcelain"], cwd=project, capture_output=True, text=True).stdout.strip() == ""


@pytest.mark.parametrize("path", ["outside.txt", "../outside.txt"])
def test_write_outside_default_prefix_is_refused_with_allowed_prefix(path, project):
    server = ProtocolMCPServer(_protocol(tools=["write"]), str(project), mode_id="author")
    with pytest.raises(MCPError, match=r"allowed prefixes: \.snodo/"):
        server.call_tool("write_file", {"path": path, "content": "no"})


def test_write_through_symlink_out_of_project_is_refused(project, tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (project / ".snodo" / "escape").symlink_to(outside, target_is_directory=True)
    server = ProtocolMCPServer(_protocol(tools=["write"]), str(project), mode_id="author")

    with pytest.raises(MCPError, match=r"allowed prefixes: \.snodo/"):
        server.call_tool("write_file", {"path": ".snodo/escape/pwned", "content": "no"})
    assert not (outside / "pwned").exists()


def test_protocol_prefixes_replace_default_and_directory_is_refused(project):
    server = ProtocolMCPServer(
        _protocol(tools=["write"], prefixes=["notes/"]), str(project), mode_id="author"
    )
    assert server.call_tool("write_file", {"path": "notes/a.txt", "content": "ok"})["bytes"] == 2
    with pytest.raises(MCPError, match="allowed prefixes: notes/"):
        server.call_tool("write_file", {"path": ".snodo/blocked", "content": "no"})
    (project / "notes" / "folder").mkdir()
    with pytest.raises(MCPError, match="names a directory"):
        server.call_tool("write_file", {"path": "notes/folder", "content": "no"})


def test_successful_write_audit_has_path_bytes_hash_mode_and_session(project):
    audit = AuditLog(str(project / ".snodo" / "audit.log"), project_id="p_test")
    from types import SimpleNamespace
    from unittest.mock import patch

    server = ProtocolMCPServer(_protocol(tools=["write"]), str(project), mode_id="author", audit_log=audit)
    content = "audited bytes"
    with patch("snodo.infrastructure.session.SessionManager.get_active_session", return_value=SimpleNamespace(session_id="sess_test")):
        server.call_tool("write_file", {"path": ".snodo/spec.md", "content": content})

    event = next(
        event for event in audit.get_history()
        if event.event_type == "tool_call" and event.data.get("op") == "file_written"
    )
    assert event.data["path"] == ".snodo/spec.md"
    assert event.data["bytes"] == len(content.encode())
    assert event.data["content_hash"] == hashlib.sha256(content.encode()).hexdigest()
    assert event.data["mode"] == "author"
    assert event.data["session_id"] == "sess_test"


def test_write_creates_the_project_audit_stream_when_not_injected(project):
    server = ProtocolMCPServer(_protocol(tools=["write"]), str(project), mode_id="author")
    server.call_tool("write_file", {"path": ".snodo/spec.md", "content": "saved"})

    audit = AuditLog(str(project / ".snodo" / "audit.log"), project_id="p_test")
    events = audit.get_history()
    assert any(
        event.event_type == "tool_call" and event.data.get("op") == "file_written"
        for event in events
    )
