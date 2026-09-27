"""MCP Apps watch_job registration, text fallback, and terminal refresh logic."""

import asyncio
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from snodo.mcp.job_handlers import JobToolHandler
from snodo.mcp.server import ProtocolMCPServer
from snodo.mcp.tools import JOB_OBSERVATION_TOOLS, MODE_TOOL_MAP
from snodo.mcp.transport import build_fastmcp_server
from snodo.mcp.watch_job import WATCH_JOB_HTML, WATCH_JOB_RESOURCE_URI
from snodo.protocols import template_protocol


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".snodo").mkdir()
    (root / "README.md").write_text("test", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


@pytest.fixture
def project(tmp_path):
    _init_repo(tmp_path)
    return tmp_path


def test_watch_job_text_snapshot_uses_existing_status_and_logs():
    handler = JobToolHandler("/project")
    status = {
        "id": "j_abc", "status": "running", "created_at": 100.0,
        "started_at": 101.0, "completed_at": None,
    }
    with patch.object(handler, "handle_get_job_status", return_value=status), patch.object(
        handler, "handle_get_job_logs", return_value={"log": "tool call\npatch\ntodo"}
    ) as logs:
        result = handler.handle_watch_job({"job_id": "j_abc"})
    assert "Job j_abc — running" in result
    assert "Last output lines:" in result
    assert "tool call\npatch\ntodo" in result
    logs.assert_called_once_with({"job_id": "j_abc", "tail": 10})


def test_watch_job_is_a_dispatch_observer_and_uses_only_job_tools():
    assert "watch_job" in MODE_TOOL_MAP["dispatch"]
    assert "watch_job" in JOB_OBSERVATION_TOOLS
    assert all(tool in JOB_OBSERVATION_TOOLS for tool in ("get_job_status", "get_job_logs"))


def test_watch_tool_resource_link_and_resource_metadata(project):
    server = ProtocolMCPServer(template_protocol("greenfield"), str(project), mode_id="build")
    fastmcp = build_fastmcp_server(server)
    tools = {tool.name: tool for tool in asyncio.run(fastmcp.list_tools())}
    assert tools["watch_job"].meta["ui"]["resourceUri"] == WATCH_JOB_RESOURCE_URI

    resources = asyncio.run(fastmcp.read_resource(WATCH_JOB_RESOURCE_URI))
    assert resources[0].mime_type == "text/html;profile=mcp-app"
    assert resources[0].meta["ui"]["csp"] == {}
    assert "ui/initialize" in resources[0].content
    assert "tools/call" in resources[0].content


def test_watch_tool_and_resource_follow_capability_gating(project):
    protocol = template_protocol("greenfield")
    build = ProtocolMCPServer(protocol, str(project), mode_id="build")
    reviewer = ProtocolMCPServer(protocol, str(project), mode_id="decide")
    assert "watch_job" in {tool["name"] for tool in build.get_tools()}
    assert "watch_job" not in {tool["name"] for tool in reviewer.get_tools()}

    fastmcp = build_fastmcp_server(reviewer)
    names = {tool.name for tool in asyncio.run(fastmcp.list_tools())}
    assert "watch_job" not in names
    with pytest.raises(Exception, match="Unknown resource"):
        asyncio.run(fastmcp.read_resource(WATCH_JOB_RESOURCE_URI))


def test_watch_page_stops_for_existing_final_job_statuses():
    source = re.search(r"function shouldStopWatching\(status\) \{[^}]+\}", WATCH_JOB_HTML)
    assert source
    script = 'const FINAL_STATUSES = ["completed", "failed", "cancelled", "unmerged"];\n' + source.group(0) + "\n" + "\n".join(
        f'if (shouldStopWatching("{status}") !== true) process.exit(1);'
        for status in ("completed", "failed", "cancelled", "unmerged")
    ) + '\nif (shouldStopWatching("running") !== false) process.exit(2);'
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
