"""Tests for Protocol-Driven MCP Server + FastMCP transport bridge.

FILE: tests/mcp/test_server.py

Tests ProtocolMCPServer (tool resolution, WF1 enforcement, mode filtering)
and the FastMCP transport bridge (build_fastmcp_server, tool handler delegation).
"""

import inspect
import json
import tempfile
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from snodo.compiler.models import Protocol
from snodo.core.interfaces import ValidatorResult
from snodo.infrastructure.tokens import TokenIssuer
from snodo.mcp.server import (
    ProtocolMCPServer,
    MCPError,
    TOOL_REGISTRY,
    MODE_TOOL_MAP,
)
from snodo.mcp.transport import build_fastmcp_server, _make_tool_handler, _build_instructions


# === Fixtures ===

MINIMAL_PROTOCOL_DATA = {
    "protocol_id": "test",
    "name": "Test Protocol",
    "version": "1.0.0",
    "modes": [
        {
            "mode_id": "producer",
            "name": "Producer",
            "tools": ["edit", "test"],
            "validators": ["security"],
        },
        {
            "mode_id": "reviewer",
            "name": "Reviewer",
            "tools": ["review", "approve"],
            "validators": ["security"],
        },
    ],
    "validators": [
        {
            "validator_id": "security",
            "validator_type": "security",
            "criteria": ["Check security"],
        },
    ],
    "disagreement_policy": "unanimous",
    "initial_mode": "producer",
}


@pytest.fixture
def protocol():
    return Protocol(**MINIMAL_PROTOCOL_DATA)


@pytest.fixture
def project_dir():
    d = tempfile.mkdtemp()
    # Init git repo for GitMCP
    subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=d, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=d, capture_output=True, check=True)
    readme = Path(d) / "README.md"
    readme.write_text("test")
    subprocess.run(["git", "add", "."], cwd=d, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=d, capture_output=True, check=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def server(protocol, project_dir):
    return ProtocolMCPServer(protocol, project_dir)


@pytest.fixture
def producer_server(protocol, project_dir):
    return ProtocolMCPServer(protocol, project_dir, mode_id="producer")


@pytest.fixture
def reviewer_server(protocol, project_dir):
    return ProtocolMCPServer(protocol, project_dir, mode_id="reviewer")


# === Tool Resolution ===

class TestToolResolution:
    def test_all_modes_resolves_all_tools(self, server):
        tools = server.get_tools()
        names = {t["name"] for t in tools}
        # Producer (edit, test) + reviewer (review, approve) + validate_task
        assert "read_file" in names      # edit
        assert "run_tests" in names      # test
        assert "read_diff" in names      # review
        assert "stage_files" in names    # approve
        assert "commit" in names         # approve
        assert "validate_task" in names  # always present
        # write_file removed from edit capability
        assert "write_file" not in names

    def test_producer_mode_tools(self, producer_server):
        tools = producer_server.get_tools()
        names = {t["name"] for t in tools}
        # edit (read_file, list_files) + test tools
        assert "read_file" in names
        assert "list_files" in names
        assert "run_tests" in names
        assert "validate_task" in names
        # write_file/delete_file REMOVED from edit capability
        assert "write_file" not in names
        assert "delete_file" not in names
        # reviewer tools NOT present
        assert "read_diff" not in names
        assert "stage_files" not in names
        assert "commit" not in names

    def test_reviewer_mode_tools(self, reviewer_server):
        tools = reviewer_server.get_tools()
        names = {t["name"] for t in tools}
        # review + approve tools
        assert "read_file" in names
        assert "read_diff" in names
        assert "get_status" in names
        assert "stage_files" in names
        assert "commit" in names
        # write/delete not available
        assert "write_file" not in names
        assert "delete_file" not in names

    def test_invalid_mode_raises(self, protocol, project_dir):
        with pytest.raises(MCPError, match="Mode not found"):
            ProtocolMCPServer(protocol, project_dir, mode_id="nonexistent")

    def test_validate_task_always_present(self, producer_server, reviewer_server):
        for srv in [producer_server, reviewer_server]:
            names = {t["name"] for t in srv.get_tools()}
            assert "validate_task" in names

    def test_tool_schemas_have_required_fields(self, server):
        tools = server.get_tools()
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool


# === WF1 Enforcement ===

class TestWF1Enforcement:
    def test_read_tools_work_without_token(self, server):
        # read_file requires no token
        (Path(server.project_root) / "hello.txt").write_text("world")
        result = server.call_tool("read_file", {"path": "hello.txt"})
        assert result == "world"

    def test_mutating_tools_rejected_without_token(self, server):
        with pytest.raises(MCPError, match="WF1 violation"):
            server.call_tool("stage_files", {"paths": ["test.txt"]})

    def test_commit_rejected_without_token(self, server):
        with pytest.raises(MCPError, match="WF1 violation"):
            server.call_tool("commit", {"message": "test"})

    def test_stage_files_rejected_without_token(self, server):
        with pytest.raises(MCPError, match="WF1 violation"):
            server.call_tool("stage_files", {"paths": ["file.txt"]})

    def test_commit_works_after_validate(self, server):
        # Issue token via validate_task
        result = server.call_tool("validate_task", {"task_id": "t1"})
        assert result["token_issued"] is True

        # Create a file to stage and commit
        (Path(server.project_root) / "new.txt").write_text("hello")
        server.call_tool("stage_files", {"paths": ["new.txt"]})
        server.call_tool("commit", {"message": "test commit"})

    def test_invalid_token_rejected(self, server):
        # Create a token with a different secret so verification fails
        rogue = TokenIssuer(secret="rogue_secret_key_32bytes_longer!", ttl_seconds=3600)
        rogue_token = rogue.issue_token(
            "t1",
            [ValidatorResult(validator_id="sec", severity="pass", justification="ok")],
        )
        server._validation_token = rogue_token
        with pytest.raises(MCPError, match="WF1 violation.*invalid"):
            server.call_tool("stage_files", {"paths": ["x.txt"]})

    def test_validate_task_returns_results(self, server):
        result = server.call_tool("validate_task", {"task_id": "t1"})
        assert "results" in result
        assert "token_issued" in result
        assert any(r["validator_id"] == "security" for r in result["results"])

    def test_validate_task_requires_task_id(self, server):
        with pytest.raises(MCPError, match="requires task_id"):
            server.call_tool("validate_task", {})

    def test_unknown_tool_rejected(self, server):
        with pytest.raises(MCPError, match="Unknown tool"):
            server.call_tool("nonexistent_tool", {})


# === Tool Execution ===

class TestToolExecution:
    def test_list_files(self, server):
        result = server.call_tool("list_files", {"directory": "."})
        assert isinstance(result, list)
        assert "README.md" in result

    def test_read_file(self, server):
        result = server.call_tool("read_file", {"path": "README.md"})
        assert result == "test"

    def test_run_tests(self, server):
        result = server.call_tool("run_tests", {"test_path": "tests/"})
        assert hasattr(result, "severity") or isinstance(result, ValidatorResult)

    def test_get_status(self, server):
        # get_status is available when all modes served
        result = server.call_tool("get_status", {})
        assert isinstance(result, str)

    def test_tool_execution_error_wrapped(self, server):
        with pytest.raises(MCPError, match="Tool execution failed"):
            server.call_tool("read_file", {"path": "nonexistent_file.xyz"})


# === MODE_TOOL_MAP coverage ===

class TestModeToolMap:
    def test_all_mode_tools_exist_in_registry(self):
        """Every concrete tool referenced by MODE_TOOL_MAP exists in TOOL_REGISTRY."""
        for mode_tool, concrete_tools in MODE_TOOL_MAP.items():
            for tool_name in concrete_tools:
                assert tool_name in TOOL_REGISTRY, (
                    f"MODE_TOOL_MAP['{mode_tool}'] references '{tool_name}' "
                    f"which is not in TOOL_REGISTRY"
                )

    def test_all_registry_tools_have_required_keys(self):
        required_keys = {"description", "inputSchema", "requires_token", "mcp", "method"}
        for name, schema in TOOL_REGISTRY.items():
            assert required_keys.issubset(schema.keys()), (
                f"TOOL_REGISTRY['{name}'] missing keys: {required_keys - schema.keys()}"
            )


# === FastMCP Bridge ===

class TestFastMCPBridge:
    def test_build_creates_fastmcp_instance(self, server):
        from mcp.server.fastmcp import FastMCP
        mcp = build_fastmcp_server(server)
        assert isinstance(mcp, FastMCP)

    def test_build_server_name(self, server):
        mcp = build_fastmcp_server(server)
        assert mcp.name == "snodo-test"

    def test_build_with_mode_includes_mode_in_name(self, producer_server):
        mcp = build_fastmcp_server(producer_server)
        assert "producer" in mcp.name

    def test_tools_registered_on_fastmcp(self, server):
        """All protocol tools are registered on FastMCP."""
        import asyncio
        mcp = build_fastmcp_server(server)
        tools = asyncio.run(mcp.list_tools())
        tool_names = {t.name for t in tools}

        # Should have all resolved tools
        assert "read_file" in tool_names
        assert "validate_task" in tool_names
        # write_file removed from edit capability
        assert "write_file" not in tool_names

    def test_fastmcp_tool_schemas_match(self, server):
        """FastMCP tool schemas reflect TOOL_REGISTRY schemas."""
        import asyncio
        mcp = build_fastmcp_server(server)
        tools = asyncio.run(mcp.list_tools())
        tool_map = {t.name: t for t in tools}

        # read_file should require 'path' parameter
        rf = tool_map["read_file"]
        assert "path" in rf.inputSchema.get("properties", {})
        assert "path" in rf.inputSchema.get("required", [])

    def test_tool_handler_delegates_read(self, server):
        """Tool handler delegates to protocol_server.call_tool."""
        (Path(server.project_root) / "test.txt").write_text("hello")

        tool_info = next(t for t in server.get_tools() if t["name"] == "read_file")
        handler = _make_tool_handler(server, tool_info)
        result = handler(path="test.txt")
        assert result == "hello"

    def test_tool_handler_wf1_error_propagates(self, server):
        """WF1 violations propagate as MCPError from handler."""
        tool_info = next(t for t in server.get_tools() if t["name"] == "stage_files")
        handler = _make_tool_handler(server, tool_info)

        with pytest.raises(MCPError, match="WF1"):
            handler(paths=["test.txt"])

    def test_tool_handler_returns_json_for_dicts(self, server):
        """Dict results are serialized as JSON (async handler for slow tools)."""
        import asyncio
        tool_info = next(t for t in server.get_tools() if t["name"] == "validate_task")
        handler = _make_tool_handler(server, tool_info)
        result = asyncio.run(handler(task_id="t1"))

        parsed = json.loads(result)
        assert "token_issued" in parsed

    def test_handler_signature_matches_schema(self):
        """Handler __signature__ matches the inputSchema properties."""
        tool_info = {
            "name": "test_tool",
            "description": "Test",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "count": {"type": "integer"},
                    "flag": {"type": "boolean", "default": False},
                },
                "required": ["path"],
            },
        }

        handler = _make_tool_handler(MagicMock(), tool_info)
        sig = inspect.signature(handler)

        assert "path" in sig.parameters
        assert "count" in sig.parameters
        assert "flag" in sig.parameters
        assert sig.parameters["path"].annotation == str
        assert sig.parameters["count"].annotation == int
        assert sig.parameters["flag"].annotation == bool
        assert sig.parameters["flag"].default is False
        # path is required (no default)
        assert sig.parameters["path"].default is inspect.Parameter.empty


# === CLI serve command ===

class TestCLIServe:
    @pytest.fixture
    def initialized_project(self):
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=d, capture_output=True, check=True)
        readme = Path(d) / "README.md"
        readme.write_text("test")
        subprocess.run(["git", "add", "."], cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=d, capture_output=True, check=True)

        # Create .snodo/protocol.yml
        snodo_dir = Path(d) / ".snodo"
        snodo_dir.mkdir()
        protocol = snodo_dir / "protocol.yml"
        import yaml
        protocol.write_text(yaml.dump(MINIMAL_PROTOCOL_DATA))

        import os
        original_cwd = os.getcwd()
        os.chdir(d)
        yield Path(d)
        os.chdir(original_cwd)
        shutil.rmtree(d, ignore_errors=True)

    def test_serve_help(self, capsys):
        from snodo.cli.main import main
        result = main(["serve", "--help"])
        assert result == 0
        out = capsys.readouterr().out
        assert "Start MCP server" in out

    def test_serve_invalid_mode(self, initialized_project, capsys):
        from snodo.cli.main import main
        result = main(["serve", "--mode", "nonexistent"])
        assert result == 1
        err = capsys.readouterr().err
        assert "not found" in err

    def test_serve_missing_protocol(self, capsys):
        import os
        d = tempfile.mkdtemp()
        original = os.getcwd()
        os.chdir(d)
        try:
            from snodo.cli.main import main
            result = main(["serve"])
            assert result == 1
        finally:
            os.chdir(original)
            shutil.rmtree(d, ignore_errors=True)

    def test_serve_stdio_runs_fastmcp(self, initialized_project):
        """Test serve with stdio transport creates FastMCP and runs it."""
        from snodo.cli.main import serve_command
        import argparse

        args = argparse.Namespace(
            protocol=".snodo/protocol.yml",
            mode=None,
            transport="stdio",
            port=8080,
            install=False,
            uninstall=False,
            uninstall_all=False,
            project_name=None,
        )

        with patch("snodo.mcp.transport.build_fastmcp_server") as mock_build:
            mock_mcp = MagicMock()
            mock_build.return_value = mock_mcp
            result = serve_command(args)

        assert result == 0
        mock_mcp.run.assert_called_once_with(transport="stdio")


# === Solo Protocol & New Tool Registrations ===

class TestSoloProtocolTools:
    """Tests for merge_branch, delete_branch registration and solo protocol."""

    def test_merge_branch_in_tool_registry(self):
        assert "merge_branch" in TOOL_REGISTRY
        assert TOOL_REGISTRY["merge_branch"]["mcp"] == "git"
        assert TOOL_REGISTRY["merge_branch"]["method"] == "merge_branch"
        assert TOOL_REGISTRY["merge_branch"]["requires_token"] is True

    def test_delete_branch_in_tool_registry(self):
        assert "delete_branch" in TOOL_REGISTRY
        assert TOOL_REGISTRY["delete_branch"]["mcp"] == "git"
        assert TOOL_REGISTRY["delete_branch"]["method"] == "delete_branch"
        assert TOOL_REGISTRY["delete_branch"]["requires_token"] is True

    def test_commit_tool_mapping(self):
        assert "commit" in MODE_TOOL_MAP
        assert "stage_files" in MODE_TOOL_MAP["commit"]
        assert "commit" in MODE_TOOL_MAP["commit"]

    def test_merge_tool_mapping_includes_new_tools(self):
        assert "merge_branch" in MODE_TOOL_MAP["merge"]
        assert "delete_branch" in MODE_TOOL_MAP["merge"]
        assert "create_branch" in MODE_TOOL_MAP["merge"]
        assert "stage_files" in MODE_TOOL_MAP["merge"]
        assert "commit" in MODE_TOOL_MAP["merge"]

    def test_solo_protocol_exposes_merge_tools(self, project_dir):
        """Solo protocol producer gets merge_branch and delete_branch tools."""
        import yaml
        from snodo.cli.commands import SOLO_PROTOCOL

        data = yaml.safe_load(SOLO_PROTOCOL)
        protocol = Protocol(**data)
        server = ProtocolMCPServer(protocol, project_dir, mode_id="producer")

        tool_names = {t["name"] for t in server.get_tools()}
        assert "merge_branch" in tool_names
        assert "delete_branch" in tool_names
        assert "create_branch" in tool_names
        assert "stage_files" in tool_names
        assert "commit" in tool_names
        assert "dispatch_task" in tool_names
        assert "run_tests" in tool_names
        assert "validate_task" in tool_names
        # write_file removed from edit capability
        assert "write_file" not in tool_names

    def test_team_protocol_producer_no_merge_tools(self, project_dir):
        """Team protocol producer does NOT get merge_branch / delete_branch."""
        import yaml
        from snodo.cli.commands import TEAM_PROTOCOL

        data = yaml.safe_load(TEAM_PROTOCOL)
        protocol = Protocol(**data)
        server = ProtocolMCPServer(protocol, project_dir, mode_id="producer")

        tool_names = {t["name"] for t in server.get_tools()}
        assert "merge_branch" not in tool_names
        assert "delete_branch" not in tool_names


# === Dispatch Task ===

class TestDispatchTask:
    """Tests for dispatch_task capability."""

    DISPATCH_PROTOCOL_DATA = {
        "protocol_id": "dispatch_test",
        "name": "Dispatch Test",
        "version": "1.0.0",
        "modes": [
            {
                "mode_id": "producer",
                "name": "Producer",
                "tools": ["edit", "dispatch", "test"],
                "validators": ["security"],
            },
        ],
        "validators": [
            {
                "validator_id": "security",
                "validator_type": "security",
                "criteria": ["Check security"],
            },
        ],
        "disagreement_policy": "unanimous",
        "initial_mode": "producer",
    }

    @pytest.fixture
    def dispatch_server(self, project_dir):
        protocol = Protocol(**self.DISPATCH_PROTOCOL_DATA)
        return ProtocolMCPServer(protocol, project_dir, mode_id="producer")

    def test_dispatch_task_in_tool_registry(self):
        assert "dispatch_task" in TOOL_REGISTRY
        assert TOOL_REGISTRY["dispatch_task"]["requires_token"] is True
        assert TOOL_REGISTRY["dispatch_task"]["mcp"] is None
        assert TOOL_REGISTRY["dispatch_task"]["method"] is None

    def test_dispatch_in_mode_tool_map(self):
        assert "dispatch" in MODE_TOOL_MAP
        assert "dispatch_task" in MODE_TOOL_MAP["dispatch"]

    def test_dispatch_task_requires_token(self, dispatch_server):
        with pytest.raises(MCPError, match="WF1 violation"):
            dispatch_server.call_tool("dispatch_task", {"task_spec": "test"})

    def test_dispatch_task_submits_to_jobmanager(self, dispatch_server):
        """dispatch_task submits to JobManager and returns the job_id."""
        dispatch_server.call_tool("validate_task", {"task_id": "t1"})
        with patch("snodo.jobs.JobManager") as mock_jm_cls:
            mock_jm = MagicMock()
            mock_jm.submit.return_value = "j_abc123"
            mock_jm_cls.return_value = mock_jm

            result = dispatch_server.call_tool(
                "dispatch_task", {"task_spec": "implement feature"}
            )

        assert result["status"] == "accepted"
        assert result["task_id"] == "j_abc123"
        assert result["task_spec"] == "implement feature"
        mock_jm_cls.assert_called_once_with(dispatch_server.project_root)
        mock_jm.submit.assert_called_once()
        submitted_args = mock_jm.submit.call_args[0][0]
        assert submitted_args["description"] == "implement feature"
        assert submitted_args["cwd"] == dispatch_server.project_root
        assert submitted_args["mode"] == "producer"

    def test_dispatch_task_sets_mode_from_server(self, dispatch_server):
        """dispatch_task includes the server's mode_id in submitted args."""
        dispatch_server.call_tool("validate_task", {"task_id": "t2"})
        with patch("snodo.jobs.JobManager") as mock_jm_cls:
            mock_jm = MagicMock()
            mock_jm.submit.return_value = "j_mode_X"
            mock_jm_cls.return_value = mock_jm

            dispatch_server.call_tool(
                "dispatch_task", {"task_spec": "mode-aware task"}
            )

        submitted_args = mock_jm.submit.call_args[0][0]
        assert submitted_args["mode"] == "producer"

    def test_dispatch_task_requires_task_spec(self, dispatch_server):
        dispatch_server.call_tool("validate_task", {"task_id": "t1"})
        with pytest.raises(MCPError, match="requires task_spec"):
            dispatch_server.call_tool("dispatch_task", {})

    def test_write_file_not_in_any_mode_tool_map(self):
        """write_file must NOT appear in any MODE_TOOL_MAP entry."""
        for mode_tool, concrete_tools in MODE_TOOL_MAP.items():
            assert "write_file" not in concrete_tools, (
                f"write_file found in MODE_TOOL_MAP['{mode_tool}']"
            )

    def test_delete_file_not_in_any_mode_tool_map(self):
        """delete_file must NOT appear in any MODE_TOOL_MAP entry."""
        for mode_tool, concrete_tools in MODE_TOOL_MAP.items():
            assert "delete_file" not in concrete_tools, (
                f"delete_file found in MODE_TOOL_MAP['{mode_tool}']"
            )

    def test_job_tools_in_registry(self):
        """get_job_status, list_jobs, get_job_logs are registered."""
        for name in ("get_job_status", "list_jobs", "get_job_logs"):
            assert name in TOOL_REGISTRY, f"{name} missing from TOOL_REGISTRY"
            assert not TOOL_REGISTRY[name]["requires_token"]
            assert TOOL_REGISTRY[name]["mcp"] is None

    def test_job_tools_in_dispatch_map(self):
        """All three job tools are in MODE_TOOL_MAP['dispatch']."""
        dispatch_tools = MODE_TOOL_MAP["dispatch"]
        for name in ("get_job_status", "list_jobs", "get_job_logs"):
            assert name in dispatch_tools

    def test_get_job_status_missing_id(self, dispatch_server):
        with pytest.raises(MCPError, match="requires job_id"):
            dispatch_server.call_tool("get_job_status", {})

    def test_get_job_status_returns_shape(self, dispatch_server):
        with patch("snodo.jobs.JobManager") as mock_cls:
            mock_jm = MagicMock()
            mock_jm.get_status.return_value = {
                "id": "j_abc", "status": "completed", "pid": 12345,
                "created_at": 100.0, "started_at": 101.0,
                "completed_at": 105.0, "exit_code": 0,
                "task": {"description": "test", "mode": "producer"},
            }
            mock_cls.return_value = mock_jm
            result = dispatch_server.call_tool(
                "get_job_status", {"job_id": "j_abc"}
            )

        assert result["status"] == "completed"
        assert result["id"] == "j_abc"
        assert result["exit_code"] == 0
        assert result["task"]["description"] == "test"

    def test_get_job_status_not_found(self, dispatch_server):
        with patch("snodo.jobs.JobManager") as mock_cls:
            from snodo.jobs import JobError
            mock_jm = MagicMock()
            mock_jm.get_status.side_effect = JobError("not found")
            mock_cls.return_value = mock_jm
            with pytest.raises(MCPError, match="Job not found"):
                dispatch_server.call_tool(
                    "get_job_status", {"job_id": "j_bad"}
                )

    def test_list_jobs_returns_array(self, dispatch_server):
        with patch("snodo.jobs.JobManager") as mock_cls:
            mock_jm = MagicMock()
            mock_jm.list_jobs.return_value = [
                {"id": "j_1", "status": "completed",
                 "description": "task A", "created_at": 100.0},
                {"id": "j_2", "status": "running",
                 "description": "task B", "created_at": 200.0},
            ]
            mock_cls.return_value = mock_jm
            result = dispatch_server.call_tool("list_jobs", {})

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["id"] == "j_1"
        assert result[1]["status"] == "running"

    def test_get_job_logs_missing_id(self, dispatch_server):
        with pytest.raises(MCPError, match="requires job_id"):
            dispatch_server.call_tool("get_job_logs", {})

    def test_get_job_logs_defaults(self, dispatch_server):
        with patch("snodo.jobs.JobManager") as mock_cls:
            mock_jm = MagicMock()
            mock_jm.get_logs.return_value = "line1\nline2\n"
            mock_cls.return_value = mock_jm
            result = dispatch_server.call_tool(
                "get_job_logs", {"job_id": "j_abc"}
            )

        assert result["job_id"] == "j_abc"
        assert result["stream"] == "stdout"
        assert result["tail"] == 50
        assert "line1" in result["log"]
        mock_jm.get_logs.assert_called_once_with(
            "j_abc", stream="stdout", tail=50
        )

    def test_get_job_logs_custom_stream(self, dispatch_server):
        with patch("snodo.jobs.JobManager") as mock_cls:
            mock_jm = MagicMock()
            mock_jm.get_logs.return_value = "error line\n"
            mock_cls.return_value = mock_jm
            result = dispatch_server.call_tool(
                "get_job_logs",
                {"job_id": "j_abc", "stream": "stderr", "tail": 10},
            )

        assert result["stream"] == "stderr"
        assert result["tail"] == 10
        mock_jm.get_logs.assert_called_once_with(
            "j_abc", stream="stderr", tail=10
        )

    def test_get_job_logs_not_found(self, dispatch_server):
        with patch("snodo.jobs.JobManager") as mock_cls:
            from snodo.jobs import JobError
            mock_jm = MagicMock()
            mock_jm.get_logs.side_effect = JobError("not found")
            mock_cls.return_value = mock_jm
            with pytest.raises(MCPError, match="Job not found"):
                dispatch_server.call_tool(
                    "get_job_logs", {"job_id": "j_bad"}
                )

    def test_model_tools_in_registry(self):
        """list_models and resolve_model are registered."""
        for name in ("list_models", "resolve_model"):
            assert name in TOOL_REGISTRY, f"{name} missing from TOOL_REGISTRY"
            assert not TOOL_REGISTRY[name]["requires_token"]
            assert TOOL_REGISTRY[name]["mcp"] is None

    def test_model_tools_in_edit_mode(self):
        """Both model tools are in MODE_TOOL_MAP['edit']."""
        edit_tools = MODE_TOOL_MAP["edit"]
        for name in ("list_models", "resolve_model"):
            assert name in edit_tools


# === Workspace Scoping ===

class TestWorkspaceScoping:
    """Verify workspace tools are scoped to project root."""

    def test_list_files_returns_project_files(self, server, project_dir):
        """list_files('.') returns project files, not system root."""
        result = server.call_tool("list_files", {"directory": "."})
        assert isinstance(result, list)
        # Project was initialized with README.md
        assert "README.md" in result
        # Should NOT contain system directories
        assert "usr" not in result
        assert "etc" not in result

    def test_list_files_sees_new_file(self, server, project_dir):
        """list_files sees files created in project root."""
        (Path(project_dir) / "hello.txt").write_text("hi")
        result = server.call_tool("list_files", {"directory": "."})
        assert "hello.txt" in result

    def test_read_file_within_project(self, server, project_dir):
        """read_file reads from project root."""
        result = server.call_tool("read_file", {"path": "README.md"})
        assert result == "test"

    def test_read_file_traversal_blocked(self, server):
        """Path traversal via read_file is blocked by workspace validation."""
        with pytest.raises(MCPError, match="Tool execution failed"):
            server.call_tool("read_file", {"path": "../../etc/passwd"})

    def test_list_files_traversal_blocked(self, server):
        """Path traversal via list_files is blocked."""
        with pytest.raises(MCPError, match="Tool execution failed"):
            server.call_tool("list_files", {"directory": "../../../"})

    def test_read_file_absolute_outside_blocked(self, server):
        """Absolute path outside project root is blocked."""
        with pytest.raises(MCPError, match="Tool execution failed"):
            server.call_tool("read_file", {"path": "/etc/passwd"})


class TestDeriveProjectRoot:
    """Test _derive_project_root in serve_cmd."""

    def test_standard_snodo_layout(self):
        """Protocol at <project>/.snodo/protocol.yml → project root is <project>."""
        from snodo.cli.commands.serve_cmd import _derive_project_root

        with tempfile.TemporaryDirectory() as tmpdir:
            snodo_dir = Path(tmpdir) / ".snodo"
            snodo_dir.mkdir()
            proto_file = snodo_dir / "protocol.yml"
            proto_file.write_text("test")

            root = _derive_project_root(str(proto_file))
            assert root == str(Path(tmpdir).resolve())

    def test_non_standard_protocol_path(self):
        """Protocol at <dir>/protocol.yml → project root is <dir>."""
        from snodo.cli.commands.serve_cmd import _derive_project_root

        with tempfile.TemporaryDirectory() as tmpdir:
            proto_file = Path(tmpdir) / "protocol.yml"
            proto_file.write_text("test")

            root = _derive_project_root(str(proto_file))
            assert root == str(Path(tmpdir).resolve())

    def test_relative_path_resolves(self):
        """Relative protocol path is resolved to absolute."""
        from snodo.cli.commands.serve_cmd import _derive_project_root
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            snodo_dir = Path(tmpdir) / ".snodo"
            snodo_dir.mkdir()
            proto_file = snodo_dir / "protocol.yml"
            proto_file.write_text("test")

            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                root = _derive_project_root(".snodo/protocol.yml")
                assert root == str(Path(tmpdir).resolve())
            finally:
                os.chdir(original_cwd)


# === SSE serve fixes ===


class TestServePortAndProxy:
    """Test port passthrough and FORWARDED_ALLOW_IPS in _run_server."""

    def test_port_passed_to_fastmcp_settings(self):
        """Port arg is set on mcp.settings.port before run."""
        from snodo.cli.commands.serve_cmd import _run_server
        from unittest.mock import MagicMock, patch, PropertyMock
        from types import SimpleNamespace

        mock_protocol = MagicMock()
        mock_protocol.protocol_id = "test"
        mock_protocol.modes = []
        mock_protocol.get_mode.return_value = None

        args = SimpleNamespace(
            protocol=".snodo/protocol.yml",
            mode=None,
            transport="sse",
            port=9999,
        )

        with patch("snodo.mcp.server.ProtocolMCPServer") as MockP:
            MockP.return_value.get_tools.return_value = []
            with patch("snodo.mcp.transport.build_fastmcp_server") as mock_build:
                mock_mcp = MagicMock()
                mock_mcp.settings.port = 8000
                mock_build.return_value = mock_mcp

                _run_server(args, mock_protocol)

                assert mock_mcp.settings.port == 9999
                mock_mcp.run.assert_called_once_with(transport="sse")

    def test_forwarded_allow_ips_set_for_sse(self):
        """FORWARDED_ALLOW_IPS is set for sse transport."""
        from snodo.cli.commands.serve_cmd import _run_server
        from unittest.mock import MagicMock, patch
        from types import SimpleNamespace
        import os

        mock_protocol = MagicMock()
        mock_protocol.protocol_id = "test"
        mock_protocol.modes = []
        mock_protocol.get_mode.return_value = None

        args = SimpleNamespace(
            protocol=".snodo/protocol.yml",
            mode=None,
            transport="sse",
            port=8080,
        )

        with patch("snodo.mcp.server.ProtocolMCPServer"):
            with patch("snodo.mcp.transport.build_fastmcp_server") as mock_build:
                mock_mcp = MagicMock()
                mock_mcp.settings.port = 8000
                mock_build.return_value = mock_mcp

                # Clear the env var before test
                os.environ.pop("FORWARDED_ALLOW_IPS", None)
                _run_server(args, mock_protocol)
                assert os.environ.get("FORWARDED_ALLOW_IPS") == "*"

    def test_forwarded_allow_ips_set_for_streamable_http(self):
        """FORWARDED_ALLOW_IPS is set for streamable-http transport."""
        from snodo.cli.commands.serve_cmd import _run_server
        from unittest.mock import MagicMock, patch
        from types import SimpleNamespace
        import os

        mock_protocol = MagicMock()
        mock_protocol.protocol_id = "test"
        mock_protocol.modes = []
        mock_protocol.get_mode.return_value = None

        args = SimpleNamespace(
            protocol=".snodo/protocol.yml",
            mode=None,
            transport="streamable-http",
            port=8080,
        )

        with patch("snodo.mcp.server.ProtocolMCPServer"):
            with patch("snodo.mcp.transport.build_fastmcp_server") as mock_build:
                mock_mcp = MagicMock()
                mock_mcp.settings.port = 8000
                mock_build.return_value = mock_mcp

                os.environ.pop("FORWARDED_ALLOW_IPS", None)
                _run_server(args, mock_protocol)
                assert os.environ.get("FORWARDED_ALLOW_IPS") == "*"

    def test_no_forwarded_allow_ips_for_stdio(self):
        """FORWARDED_ALLOW_IPS is NOT set for stdio transport."""
        from snodo.cli.commands.serve_cmd import _run_server
        from unittest.mock import MagicMock, patch
        from types import SimpleNamespace
        import os

        mock_protocol = MagicMock()
        mock_protocol.protocol_id = "test"
        mock_protocol.modes = []
        mock_protocol.get_mode.return_value = None

        args = SimpleNamespace(
            protocol=".snodo/protocol.yml",
            mode=None,
            transport="stdio",
            port=8000,
        )

        with patch("snodo.mcp.server.ProtocolMCPServer"):
            with patch("snodo.mcp.transport.build_fastmcp_server") as mock_build:
                mock_mcp = MagicMock()
                mock_mcp.settings.port = 8000
                mock_build.return_value = mock_mcp

                os.environ.pop("FORWARDED_ALLOW_IPS", None)
                _run_server(args, mock_protocol)
                assert "FORWARDED_ALLOW_IPS" not in os.environ

    def test_hint_printed_for_sse(self, capsys):
        """DIY remote access hint printed for sse transport."""
        from snodo.cli.commands.serve_cmd import _run_server
        from unittest.mock import MagicMock, patch
        from types import SimpleNamespace

        mock_protocol = MagicMock()
        mock_protocol.protocol_id = "test"
        mock_protocol.modes = []
        mock_protocol.get_mode.return_value = None

        args = SimpleNamespace(
            protocol=".snodo/protocol.yml",
            mode=None,
            transport="sse",
            port=8080,
        )

        with patch("snodo.mcp.server.ProtocolMCPServer"):
            with patch("snodo.mcp.transport.build_fastmcp_server") as mock_build:
                mock_mcp = MagicMock()
                mock_mcp.settings.port = 8000
                mock_build.return_value = mock_mcp

                _run_server(args, mock_protocol)

        out = capsys.readouterr().out
        assert "ngrok" in out
        assert "cloudflared" in out
        assert "tailscale" in out
        assert "snodo serve --tunnel" in out

    def test_no_hint_for_stdio(self, capsys):
        """No DIY hint printed for stdio transport."""
        from snodo.cli.commands.serve_cmd import _run_server
        from unittest.mock import MagicMock, patch
        from types import SimpleNamespace

        mock_protocol = MagicMock()
        mock_protocol.protocol_id = "test"
        mock_protocol.modes = []
        mock_protocol.get_mode.return_value = None

        args = SimpleNamespace(
            protocol=".snodo/protocol.yml",
            mode=None,
            transport="stdio",
            port=8000,
        )

        with patch("snodo.mcp.server.ProtocolMCPServer"):
            with patch("snodo.mcp.transport.build_fastmcp_server") as mock_build:
                mock_mcp = MagicMock()
                mock_mcp.settings.port = 8000
                mock_build.return_value = mock_mcp

                _run_server(args, mock_protocol)

        out = capsys.readouterr().out
        assert "ngrok" not in out
        assert "cloudflared" not in out
        assert "snodo serve --tunnel" not in out


# === Task 7.1: Server Audit Log Wiring ===

class TestServerAuditLog:
    """Tests for audit log wiring in ProtocolMCPServer."""

    @pytest.fixture
    def audit_log(self):
        from snodo.infrastructure.audit import AuditLog
        f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        f.close()
        audit = AuditLog(f.name)
        yield audit
        Path(f.name).unlink(missing_ok=True)

    @pytest.fixture
    def audited_server(self, project_dir, audit_log):
        protocol = Protocol(**MINIMAL_PROTOCOL_DATA)
        return ProtocolMCPServer(protocol, project_dir, audit_log=audit_log)

    def test_server_accepts_audit_log(self, audited_server, audit_log):
        assert audited_server._audit_log is audit_log

    def test_tool_call_logs_event(self, audited_server, audit_log):
        """Non-token tool call logs tool_call event."""
        audited_server.call_tool("read_file", {"path": "README.md"})
        events = audit_log.get_history(event_type="tool_call")
        assert len(events) == 1
        assert events[0].data["tool_name"] == "read_file"
        assert "args_hash" in events[0].data
        assert len(events[0].data["args_hash"]) == 16  # truncated

    def test_wf1_violation_logs_event(self, audited_server, audit_log):
        """WF1 violation logs wf1_violation event."""
        with pytest.raises(MCPError, match="WF1"):
            audited_server.call_tool("stage_files", {"paths": ["x.txt"]})
        events = audit_log.get_history(event_type="wf1_violation")
        assert len(events) == 1
        assert events[0].data["tool"] == "stage_files"
        assert events[0].data["reason"] == "no_token"

    def test_validate_task_logs_validator_results(self, audited_server, audit_log):
        """validate_task logs validator_results event."""
        audited_server.call_tool("validate_task", {"task_id": "t1"})
        events = audit_log.get_history(event_type="validator_results")
        assert len(events) == 1
        assert events[0].data["task_id"] == "t1"
        assert "validator_outcomes" in events[0].data

    def test_args_hash_truncated(self, audited_server):
        """args_hash is exactly 16 characters (truncated SHA256)."""
        h = audited_server._args_hash({"path": "big_content_here" * 100})
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_audit_log_injected_to_token_issuer(self, audited_server, audit_log):
        """TokenIssuer constructed with same audit_log."""
        assert audited_server.token_issuer._audit_log is audit_log

    def test_audit_chain_valid_after_operations(self, audited_server, audit_log):
        """Audit chain valid after multiple operations."""
        audited_server.call_tool("read_file", {"path": "README.md"})
        audited_server.call_tool("validate_task", {"task_id": "t1"})
        assert audit_log.verify_chain() is True
        assert len(audit_log.events) >= 2


# === MCP Self-Description: Instructions + Resources ===

class TestInstructions:
    """Tests for server instructions in the initialize handshake."""

    def test_instructions_built_from_protocol(self, server):
        """Instructions contain protocol-specific data."""
        instructions = _build_instructions(server)
        assert "test" in instructions  # protocol_id
        assert "1.0.0" in instructions  # version
        assert "producer" in instructions  # mode
        assert "reviewer" in instructions  # mode
        assert "security" in instructions  # validator
        assert "unanimous" in instructions  # disagreement_policy

    def test_instructions_contains_workflow_loop(self, server):
        """Instructions contain the ordered workflow loop."""
        instructions = _build_instructions(server)
        assert "validate_task" in instructions
        assert "dispatch_task" in instructions
        assert "get_job_status" in instructions
        assert "get_job_logs" in instructions

    def test_instructions_contains_async_contract(self, server):
        """Instructions explicitly state the async contract."""
        instructions = _build_instructions(server)
        assert "ASYNCHRONOUS" in instructions
        assert "poll" in instructions.lower() or "get_job_status" in instructions
        assert "dispatch" in instructions.lower()

    def test_instructions_contains_wf1(self, server):
        """Instructions describe WF1 token lifecycle."""
        instructions = _build_instructions(server)
        assert "WF1" in instructions
        assert "single-use" in instructions or "token" in instructions.lower()

    def test_instructions_contains_resource_uris(self, server):
        """Instructions point to resources for state."""
        instructions = _build_instructions(server)
        assert "snodo://protocol" in instructions
        assert "snodo://sessions" in instructions
        assert "snodo://audit" in instructions

    def test_instructions_passed_to_fastmcp(self, server):
        """FastMCP instance receives instructions."""
        mcp = build_fastmcp_server(server)
        assert mcp.instructions is not None
        assert "ASYNCHRONOUS" in mcp.instructions
        assert "test" in mcp.instructions


class TestResources:
    """Tests for MCP resources (read-only, URI-addressable)."""

    def _read_resource_content(self, mcp, uri):
        """Extract string content from FastMCP read_resource result."""
        import asyncio
        results = asyncio.run(mcp.read_resource(uri))
        # read_resource returns list[ReadResourceContents]
        return results[0].content if results else ""

    def test_protocol_resource(self, server):
        """snodo://protocol returns protocol data as JSON."""
        mcp = build_fastmcp_server(server)
        content = self._read_resource_content(mcp, "snodo://protocol")
        data = json.loads(content)
        assert data["protocol_id"] == "test"
        assert data["version"] == "1.0.0"
        assert len(data["modes"]) == 2
        assert len(data["validators"]) == 1

    def test_sessions_resource(self, server, project_dir):
        """snodo://sessions returns session list as JSON."""
        mcp = build_fastmcp_server(server)
        content = self._read_resource_content(mcp, "snodo://sessions")
        data = json.loads(content)
        assert isinstance(data, list)

    def test_session_detail_resource(self, server, project_dir):
        """snodo://sessions/{id} returns session detail."""
        from snodo.infrastructure.session import SessionManager

        # Create a session first
        mgr = SessionManager()
        session = mgr.create_session(
            mode="producer",
            project_root=project_dir,
        )

        mcp = build_fastmcp_server(server)
        content = self._read_resource_content(
            mcp, f"snodo://sessions/{session.session_id}"
        )
        data = json.loads(content)
        assert data["session_id"] == session.session_id
        assert data["mode"] == "producer"
        assert "audit_events" in data

    def test_session_detail_not_found(self, server):
        """snodo://sessions/{nonexistent} returns error JSON."""
        mcp = build_fastmcp_server(server)
        content = self._read_resource_content(
            mcp, "snodo://sessions/nonexistent_123"
        )
        data = json.loads(content)
        assert "error" in data
        assert "not found" in data["error"].lower()

    def test_audit_resource(self, server, project_dir):
        """snodo://audit returns bounded recent events."""
        from snodo.infrastructure.audit import AuditLog
        from snodo.mcp.server import ProtocolMCPServer

        f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        f.close()
        audit_log = AuditLog(f.name)

        # Add some events
        audit_log.append_event("test_event", {"key": "value"})
        audit_log.append_event("another_event", {"key2": "value2"})

        # Create server with audit log
        audited_server = ProtocolMCPServer(
            server.protocol, project_dir, audit_log=audit_log
        )

        mcp = build_fastmcp_server(audited_server)
        content = self._read_resource_content(mcp, "snodo://audit")
        data = json.loads(content)
        assert isinstance(data, list)
        assert len(data) >= 2
        assert data[-1]["event_type"] == "another_event"

        Path(f.name).unlink(missing_ok=True)

    def test_audit_resource_no_log(self, server):
        """snodo://audit with no audit log returns empty note."""
        mcp = build_fastmcp_server(server)
        content = self._read_resource_content(mcp, "snodo://audit")
        data = json.loads(content)
        assert "events" in data
        assert "note" in data

    def test_resources_listed_on_fastmcp(self, server):
        """All 4 resources are registered on FastMCP."""
        import asyncio
        mcp = build_fastmcp_server(server)
        resources = asyncio.run(mcp.list_resources())
        uris = {str(r.uri) for r in resources}
        templates = asyncio.run(mcp.list_resource_templates())
        template_uris = {t.uriTemplate for t in templates}
        all_uris = uris | template_uris
        assert "snodo://protocol" in all_uris
        assert "snodo://sessions" in all_uris
        assert "snodo://sessions/{session_id}" in all_uris
        assert "snodo://audit" in all_uris

    def test_resources_are_read_only(self, server):
        """Resources return string/JSON content, not mutable objects."""
        mcp = build_fastmcp_server(server)
        for uri in ["snodo://protocol", "snodo://sessions", "snodo://audit"]:
            content = self._read_resource_content(mcp, uri)
            assert isinstance(content, str)
            json.loads(content)  # valid JSON
