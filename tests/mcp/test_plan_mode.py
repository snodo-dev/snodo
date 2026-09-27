"""Tests for plan mode capability resolution and enforcement.

FILE: tests/mcp/test_plan_mode.py
"""

import shutil
import subprocess
import tempfile
from pathlib import Path
import pytest

from snodo.compiler.verifier import verify_protocol
from snodo.mcp.server import MODE_TOOL_MAP, MCPError, ProtocolMCPServer
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
    assert "edit" in plan_mode.tools
    assert "write" in plan_mode.tools
    assert "meta-spec" in plan_mode.validators
    assert plan_mode.transitions.get("planned") == "decide"


def test_plan_mode_resolves_planning_queue_and_write_tools(project_dir):
    """Greenfield planning mode can author plans, operate queues, and write under .snodo/."""
    proto = template_protocol("greenfield")
    server = ProtocolMCPServer(proto, project_dir, mode_id="plan")
    tools = server.get_tools()
    tool_names = {t["name"] for t in tools}

    # Plan authoring and queue operation are one capability surface.
    assert "decompose" in tool_names
    assert "generate_spec" in tool_names
    assert "validate_plan" in tool_names
    assert "run_plan" in tool_names
    assert {
        "queue_list",
        "queue_create",
        "queue_move",
        "queue_remove",
        "queue_validate",
        "queue_run",
    } <= tool_names

    # edit gives repository reads/recon, and write_file is bounded to .snodo/.
    assert "read_file" in tool_names
    assert "list_files" in tool_names
    assert "validate_task" in tool_names
    assert "recon" in tool_names
    assert "get_recon_status" in tool_names
    assert "get_recon_results" in tool_names
    assert "write_file" in tool_names

    # The planning mode still has no task-dispatch or commit authority.
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


def test_solo_producer_exposes_the_single_operator_loop(project_dir):
    """The solo template grants every capability used by the MCP loop."""
    proto = template_protocol("solo")
    producer = proto.get_mode("producer")
    assert producer is not None
    assert all(tool in MODE_TOOL_MAP for tool in producer.tools)

    names = {tool["name"] for tool in ProtocolMCPServer(
        proto, project_dir, mode_id="producer"
    ).get_tools()}
    assert {
        "recon",
        "decompose",
        "generate_spec",
        "propose_plan",
        "get_plan",
        "run_plan",
        "validate_plan",
        "queue_list",
        "queue_create",
        "queue_move",
        "queue_remove",
        "queue_validate",
        "queue_run",
        "write_file",
        "run_tests",
        "stage_files",
        "commit",
        "merge_branch",
    } <= names
    assert "delete_file" not in names


def test_solo_protocol_options_keep_safe_loop_defaults():
    proto = template_protocol("solo")
    producer = proto.get_mode("producer")

    assert producer is not None
    assert producer.concurrency == 1
    assert proto.queue.non_blocking is False
    assert proto.queue.parallel_runs == 1
    assert proto.write_allowed_prefixes == [".snodo/"]


@pytest.mark.parametrize(
    ("template", "author_mode"),
    [("team", "planner"), ("2+n", "producer")],
)
def test_team_templates_grant_plan_queue_and_write_to_author_mode(
    template, author_mode, project_dir,
):
    """Shipped team templates expose the current loop only to its author mode."""
    from snodo.mcp.server import MODE_TOOL_MAP

    protocol = template_protocol(template)
    result = verify_protocol(protocol)
    assert result.passed, f"{template} WF violations: {result.errors}"
    assert all(tool in MODE_TOOL_MAP for mode in protocol.modes for tool in mode.tools)
    assert protocol.queue.non_blocking is False
    assert protocol.queue.parallel_runs == 1
    assert protocol.write_allowed_prefixes == [".snodo/"]
    assert protocol.protected_paths == []

    author = ProtocolMCPServer(protocol, project_dir, mode_id=author_mode)
    exposed = {tool["name"] for tool in author.get_tools()}
    assert {"decompose", "generate_spec", "validate_plan", "propose_plan"} <= exposed
    assert {"queue_list", "queue_create", "queue_move", "queue_validate", "queue_run"} <= exposed
    assert "write_file" in exposed

    reviewer = ProtocolMCPServer(protocol, project_dir, mode_id="reviewer")
    reviewer_tools = {tool["name"] for tool in reviewer.get_tools()}
    assert not ({"decompose", "generate_spec", "validate_plan", "propose_plan", "queue_list", "queue_run", "write_file"} & reviewer_tools)
    assert {"stage_files", "commit", "merge_branch"} <= reviewer_tools

    # WF1's exclusive approval and merge capabilities remain reviewer-only.
    holders = {
        capability: [mode.mode_id for mode in protocol.modes if capability in mode.tools]
        for capability in ("approve", "merge")
    }
    assert holders == {"approve": ["reviewer"], "merge": ["reviewer"]}
