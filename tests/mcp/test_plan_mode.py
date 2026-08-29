"""Tests for plan mode capability resolution and enforcement.

FILE: tests/mcp/test_plan_mode.py
"""

import shutil
import subprocess
import tempfile
from pathlib import Path
import pytest

from snodo.compiler.verifier import verify_protocol
from snodo.mcp.server import MCPError, ProtocolMCPServer
from snodo.protocols import template_protocol


@pytest.fixture
def project_dir():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=d, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=d, capture_output=True, check=True)
    readme = Path(d) / "README.md"
    readme.write_text("test")
    subprocess.run(["git", "add", "."], cwd=d, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=d, capture_output=True, check=True)
    (Path(d) / ".snodo").mkdir()
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_greenfield_template_includes_plan_mode_and_passes_verification():
    """greenfield.yml template includes plan mode and passes well-formedness verification."""
    proto = template_protocol("greenfield")
    result = verify_protocol(proto)
    assert result.passed, f"greenfield.yml WF violations: {result.errors}"

    mode_ids = [m.mode_id for m in proto.modes]
    assert "plan" in mode_ids

    plan_mode = next(m for m in proto.modes if m.mode_id == "plan")
    assert plan_mode.name == "Plan"
    assert "plan" in plan_mode.tools
    assert "read" in plan_mode.tools
    assert "meta-spec" in plan_mode.validators
    assert plan_mode.transitions.get("planned") == "decide"


def test_plan_mode_resolves_exact_planner_tools(project_dir):
    """A mode declaring 'plan' resolves to exactly decompose, generate_spec, and validate_plan."""
    proto = template_protocol("greenfield")
    server = ProtocolMCPServer(proto, project_dir, mode_id="plan")
    tools = server.get_tools()
    tool_names = {t["name"] for t in tools}

    # Must contain exactly the planner tools
    assert "decompose" in tool_names
    assert "generate_spec" in tool_names
    assert "validate_plan" in tool_names

    # Plus read-only tools ('read' capability) and validate_task meta-tool
    assert "read_file" in tool_names
    assert "list_files" in tool_names
    assert "validate_task" in tool_names

    # Must NOT hold any mutating repository tools
    assert "write_file" not in tool_names
    assert "delete_file" not in tool_names
    assert "stage_files" not in tool_names
    assert "commit" not in tool_names
    assert "merge_branch" not in tool_names
    assert "delete_branch" not in tool_names
    assert "dispatch_task" not in tool_names
    assert "retry_job" not in tool_names


def test_mode_without_plan_refuses_planner_tools(project_dir):
    """A mode WITHOUT 'plan' (e.g. decide, scaffold, build) is refused planner tools."""
    proto = template_protocol("greenfield")

    for mode_id in ["decide", "scaffold", "build"]:
        server = ProtocolMCPServer(proto, project_dir, mode_id=mode_id)
        tool_names = {t["name"] for t in server.get_tools()}

        assert "decompose" not in tool_names, f"decompose granted in mode '{mode_id}'"
        assert "generate_spec" not in tool_names, f"generate_spec granted in mode '{mode_id}'"
        assert "validate_plan" not in tool_names, f"validate_plan granted in mode '{mode_id}'"

        # Attempting to call decompose in a non-plan mode raises MCPError
        with pytest.raises(MCPError, match="Unknown tool: decompose"):
            server.call_tool("decompose", {"intent": "test", "plan_name": "p"})


def test_canary_capability_refusal_denies_unauthorized_tool(project_dir):
    """Canary test asserting capability check denies decompose to a mode lacking 'plan'."""
    proto = template_protocol("greenfield")
    server = ProtocolMCPServer(proto, project_dir, mode_id="decide")

    # Assert decompose is absent from exposed tools list
    tools_list = server.get_tools()
    assert not any(t["name"] == "decompose" for t in tools_list)

    # Calling tool directly must fail closed with Unknown tool error
    with pytest.raises(MCPError, match="Unknown tool: decompose"):
        server.call_tool("decompose", {"intent": "test", "plan_name": "p"})
