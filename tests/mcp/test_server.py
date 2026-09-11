"""Tests for Protocol-Driven MCP Server + FastMCP transport bridge.

FILE: tests/mcp/test_server.py

Tests ProtocolMCPServer (tool resolution, WF1 enforcement, mode filtering)
and the FastMCP transport bridge (build_fastmcp_server, tool handler delegation).
"""

import inspect
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from snodo.compiler.models import Protocol
from snodo.core.interfaces import ValidatorResult
from snodo.infrastructure.tokens import TokenIssuer
from snodo.mcp.server import (
    MODE_TOOL_MAP,
    TOOL_REGISTRY,
    MCPError,
    ProtocolMCPServer,
)
from snodo.mcp.transport import (
    _build_instructions,
    _make_tool_handler,
    build_fastmcp_server,
)

from tests.mcp._validate_helpers import validation_passing

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
        # Issue token via validate_task (validators pass under the mock)
        with validation_passing(server):
            result = server.call_tool("validate_task", {"task_id": "t1"})
        assert result["token_issued"] is True
        assert result["status"] == "pass"

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
        with validation_passing(server):
            result = server.call_tool("validate_task", {"task_id": "t1"})
        assert "results" in result
        assert "status" in result
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
        assert sig.parameters["path"].annotation is str
        assert sig.parameters["count"].annotation is int
        assert sig.parameters["flag"].annotation is bool
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
        import argparse

        from snodo.cli.main import serve_command

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
        with validation_passing(dispatch_server):
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
        with validation_passing(dispatch_server):
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
        with validation_passing(dispatch_server):
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
                "task": {"description": "test", "mode": "producer",
                         "task_id": "T-7"},
            }
            mock_cls.return_value = mock_jm
            result = dispatch_server.call_tool(
                "get_job_status", {"job_id": "j_abc"}
            )

        assert result["status"] == "completed"
        assert result["id"] == "j_abc"
        assert result["exit_code"] == 0
        assert "task" not in result
        # the single-job tool keeps the full spec — the listing dropped it
        assert result["task_spec"] == "test"
        assert result["task_ref"] == "T-7"

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

    def test_list_jobs_returns_bounded_rows(self, dispatch_server):
        with patch("snodo.jobs.JobManager") as mock_cls:
            mock_jm = MagicMock()
            mock_jm.list_jobs.return_value = [
                {"id": "j_1", "status": "completed", "task_ref": "T-1",
                 "title": "task A", "exit_code": 0, "created_at": 100.0,
                 "started_at": 100.0, "completed_at": 130.0,
                 "duration_seconds": 30.0},
                {"id": "j_2", "status": "running", "task_ref": "",
                 "title": "task B", "exit_code": None, "created_at": 200.0,
                 "started_at": 201.0, "completed_at": None,
                 "duration_seconds": 5.0},
            ]
            mock_cls.return_value = mock_jm
            result = dispatch_server.call_tool("list_jobs", {})

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["id"] == "j_1"
        assert result[1]["status"] == "running"
        # no row is a carrier for spec prose
        assert not any("description" in row for row in result)

    def test_list_jobs_size_bounded_on_a_real_project(self, dispatch_server):
        """A project with many jobs answers the listing at bounded size."""
        import json as json_mod
        import tempfile
        from pathlib import Path as _Path
        from snodo.jobs import JobManager

        with tempfile.TemporaryDirectory() as tmp:
            (_Path(tmp) / ".snodo").mkdir()
            mgr = JobManager(tmp)
            base = 1_700_000_000.0
            spec = "# Payment retry engine\n" + "prose of the specification\n" * 120
            for i in range(40):
                jd = mgr.jobs_dir / f"j_{i:06d}"
                jd.mkdir()
                (jd / "task.json").write_text(json_mod.dumps(
                    {"description": spec, "task_id": f"T-{i}"}
                ))
                (jd / "state.json").write_text(json_mod.dumps({
                    "status": "failed", "pid": None, "created_at": base + i,
                    "started_at": base + i + 1, "completed_at": base + i + 9,
                    "exit_code": 1,
                }))
            with patch("snodo.jobs.JobManager", return_value=mgr):
                rows = dispatch_server.call_tool("list_jobs", {})

            payload = json_mod.dumps(rows)
            # 40 fat specs inline would exceed 40KB; rows are bounded
            assert len(payload) < 40 * 400
            assert "prose of the specification" not in payload
            assert rows[0]["exit_code"] == 1
            assert rows[0]["task_ref"].startswith("T-")
            assert rows[0]["title"] == "Payment retry engine"
            assert rows[0]["duration_seconds"] == 8.0

    def test_get_job_status_carries_the_full_spec(self, dispatch_server):
        """The spec is a detail of one job — available per job, still."""
        import json as json_mod
        import tempfile
        from pathlib import Path as _Path
        from snodo.jobs import JobManager

        with tempfile.TemporaryDirectory() as tmp:
            (_Path(tmp) / ".snodo").mkdir()
            mgr = JobManager(tmp)
            jd = mgr.jobs_dir / "j_spec01"
            jd.mkdir()
            spec = "# Title\n" + "prose\n" * 200
            (jd / "task.json").write_text(json_mod.dumps({"description": spec}))
            (jd / "state.json").write_text(json_mod.dumps({
                "status": "completed", "pid": None, "created_at": 1.0,
                "started_at": 2.0, "completed_at": 3.0, "exit_code": 0,
            }))
            with patch("snodo.jobs.JobManager", return_value=mgr):
                result = dispatch_server.call_tool(
                    "get_job_status", {"job_id": "j_spec01"}
                )

        assert result["task_spec"] == spec
        assert result["status"] == "completed"

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
        import os

        from snodo.cli.commands.serve_cmd import _derive_project_root

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

    @pytest.fixture(autouse=True)
    def _isolate_environ(self, monkeypatch):
        """_run_server writes FORWARDED_ALLOW_IPS into os.environ and leaves it
        set; swap in a throwaway copy so the write cannot leak into later tests
        (Fixes #200)."""
        import os

        monkeypatch.setattr(os, "environ", os.environ.copy())

    def test_port_passed_to_fastmcp_settings(self):
        """Port arg is set on mcp.settings.port before run."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from snodo.cli.commands.serve_cmd import _run_server

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
        import os
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from snodo.cli.commands.serve_cmd import _run_server

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
        import os
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from snodo.cli.commands.serve_cmd import _run_server

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
        import os
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from snodo.cli.commands.serve_cmd import _run_server

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
                assert os.getenv("FORWARDED_ALLOW_IPS") is None

    def test_hint_printed_for_sse(self, capsys):
        """DIY remote access hint printed for sse transport."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from snodo.cli.commands.serve_cmd import _run_server

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
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from snodo.cli.commands.serve_cmd import _run_server

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


# === Managed tunnel tests ===


class TestTunnelProvisioning:
    """Test tunnel config saving and API stubs."""

    def test_tunnel_config_saves_no_client_secret(self, tmp_path):
        """tunnel.json never includes client_secret."""
        from snodo.cli.commands.serve_cmd import (
            _load_tunnel_config,
            _save_tunnel_config,
        )

        project_root = str(tmp_path)
        config = {
            "hostname": "test.tunnel.snodo.dev",
            "tunnel_token": "eyJ...",
            "client_id": "abc123.access",
            "client_secret": "SECRET_DO_NOT_SAVE",
            "created_at": "2026-06-07T00:00:00Z",
        }
        _save_tunnel_config(project_root, config)

        saved = _load_tunnel_config(project_root)
        assert saved["hostname"] == "test.tunnel.snodo.dev"
        assert saved["tunnel_token"] == "eyJ..."
        assert "client_id" not in saved
        assert "client_secret" not in saved

    def test_load_missing_tunnel_config(self, tmp_path):
        """Missing tunnel.json returns empty dict."""
        from snodo.cli.commands.serve_cmd import _load_tunnel_config

        config = _load_tunnel_config(str(tmp_path))
        assert config == {}

    def test_generate_short_id_is_alphanumeric(self):
        """Short IDs are 6 alphanumeric chars."""
        from snodo.cli.commands.serve_cmd import _generate_short_id

        for _ in range(20):
            sid = _generate_short_id()
            assert len(sid) == 6
            assert sid.isalnum()

    def test_check_cloudflared_missing(self):
        """When cloudflared is not on PATH, returns False."""
        from unittest.mock import patch

        from snodo.cli.commands.serve_cmd import _check_cloudflared

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError
            assert _check_cloudflared() is False

    def test_check_cloudflared_found(self):
        """When cloudflared is on PATH, returns True."""
        from unittest.mock import patch

        from snodo.cli.commands.serve_cmd import _check_cloudflared

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            assert _check_cloudflared() is True

    # -- destination: tunnel worker, not ingest -----------------------

    @staticmethod
    def _write_cloud_config(tmp_path, body):
        (tmp_path / "config.yml").write_text(body)

    def test_provision_targets_tunnel_worker_not_ingest(self, tmp_path, monkeypatch):
        """cloud.tunnel_api_url routes provisioning; cloud.api_url (ingest)
        must never see /tunnel/provision."""
        import httpx

        from snodo.cli.commands import serve_cmd

        self._write_cloud_config(
            tmp_path,
            "cloud:\n"
            "  api_key: sndo_live_test\n"
            "  api_url: https://ingest.snodo.example.test\n"
            "  tunnel_api_url: https://tunnel.snodo.example.test\n",
        )
        monkeypatch.setenv("SNODO_HOME", str(tmp_path))

        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "hostname": "proj-all-abc123.tunnel.snodo.dev",
                "tunnel_token": "tok_x",
            }
            return resp

        monkeypatch.setattr(httpx, "post", fake_post)
        out = serve_cmd._provision_tunnel(
            "sndo_live_test", "proj", "all", "abc123", "1.2.3", 55441,
        )

        assert out["hostname"] == "proj-all-abc123.tunnel.snodo.dev"
        assert captured["url"] == "https://tunnel.snodo.example.test/tunnel/provision"
        assert "ingest.snodo.example.test" not in captured["url"]
        # Request body is unchanged by the split.
        assert captured["json"] == {
            "project_slug": "proj",
            "mode": "all",
            "short_id": "abc123",
            "snodo_version": "1.2.3",
            "port": 55441,
        }

    def test_provision_legacy_config_resolves_to_default_tunnel_host(self, tmp_path, monkeypatch):
        """A config written before the split (api_url only) still provisions
        against the tunnel host, not the ingest host it names."""
        import httpx

        from snodo.cli.commands import serve_cmd
        from snodo.config import DEFAULT_CLOUD_API_URL, DEFAULT_TUNNEL_API_URL

        self._write_cloud_config(
            tmp_path,
            "cloud:\n"
            "  api_key: sndo_live_test\n"
            f"  api_url: {DEFAULT_CLOUD_API_URL}\n"
            "  sync_enabled: true\n",
        )
        monkeypatch.setenv("SNODO_HOME", str(tmp_path))

        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"hostname": "h", "tunnel_token": "t"}
            return resp

        monkeypatch.setattr(httpx, "post", fake_post)
        serve_cmd._provision_tunnel("sndo_live_test", "proj", "all", "abc123", "1.2.3")

        assert captured["url"] == f"{DEFAULT_TUNNEL_API_URL}/tunnel/provision"
        assert DEFAULT_CLOUD_API_URL not in captured["url"]

    def test_deprovision_targets_tunnel_worker_not_ingest(self, tmp_path, monkeypatch):
        import httpx

        from snodo.cli.commands import serve_cmd

        self._write_cloud_config(
            tmp_path,
            "cloud:\n"
            "  api_key: sndo_live_test\n"
            "  api_url: https://ingest.snodo.example.test\n"
            "  tunnel_api_url: https://tunnel.snodo.example.test\n",
        )
        monkeypatch.setenv("SNODO_HOME", str(tmp_path))

        captured = {}

        def fake_delete(url, **kwargs):
            captured["url"] = url
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "ok"
            return resp

        monkeypatch.setattr(httpx, "delete", fake_delete)
        assert serve_cmd._deprovision_tunnel("sndo_live_test", "h.tunnel.snodo.dev") is True
        assert captured["url"] == "https://tunnel.snodo.example.test/tunnel/h.tunnel.snodo.dev"
        assert "ingest.snodo.example.test" not in captured["url"]

    # -- error reporting ----------------------------------------------

    def test_provision_failure_reports_actual_response(self, tmp_path, monkeypatch):
        """A non-auth failure surfaces the endpoint, status, and body —
        not a guess about the API key."""
        import httpx

        from snodo.cli.commands import serve_cmd
        from snodo.cli.commands.serve_cmd import TunnelAPIError

        self._write_cloud_config(
            tmp_path,
            "cloud:\n"
            "  api_url: https://ingest.snodo.example.test\n"
            "  tunnel_api_url: https://tunnel.snodo.example.test\n",
        )
        monkeypatch.setenv("SNODO_HOME", str(tmp_path))

        resp = MagicMock()
        resp.status_code = 400
        resp.text = '{"error": "Missing or invalid field: project_path"}'
        monkeypatch.setattr(httpx, "post", lambda url, **kw: resp)

        with pytest.raises(TunnelAPIError) as excinfo:
            serve_cmd._provision_tunnel("k", "proj", "all", "abc123", "1.2.3")

        message = str(excinfo.value)
        assert excinfo.value.status_code == 400
        assert "POST https://tunnel.snodo.example.test/tunnel/provision" in message
        assert "HTTP 400" in message
        assert "project_path" in message

    # -- 409 conflict: the cloud names the blocking tunnel ------------

    def test_extract_existing_hostname_from_json_and_free_text(self):
        from snodo.cli.commands.serve_cmd import _extract_existing_hostname

        assert _extract_existing_hostname(
            '{"error": "exists", "hostname": "ghost-all-abc123.tunnel.snodo.dev"}'
        ) == "ghost-all-abc123.tunnel.snodo.dev"
        assert _extract_existing_hostname(
            'Error: project already has tunnel ghost-all-abc123.tunnel.snodo.dev for mode all'
        ) == "ghost-all-abc123.tunnel.snodo.dev"
        assert _extract_existing_hostname('{"error": "conflict"}') is None

    def test_provision_conflict_carries_blocking_hostname(self, tmp_path, monkeypatch):
        """A 409 raises TunnelAPIError with existing_hostname set."""
        import httpx

        from snodo.cli.commands import serve_cmd
        from snodo.cli.commands.serve_cmd import TunnelAPIError

        self._write_cloud_config(
            tmp_path,
            "cloud:\n"
            "  api_url: https://ingest.snodo.example.test\n"
            "  tunnel_api_url: https://tunnel.snodo.example.test\n",
        )
        monkeypatch.setenv("SNODO_HOME", str(tmp_path))

        resp = MagicMock()
        resp.status_code = 409
        resp.text = '{"error": "tunnel exists", "hostname": "ghost-all-abc123.tunnel.snodo.dev"}'
        monkeypatch.setattr(httpx, "post", lambda url, **kw: resp)

        with pytest.raises(TunnelAPIError) as excinfo:
            serve_cmd._provision_tunnel("k", "proj", "all", "abc123", "1.2.3")

        assert excinfo.value.status_code == 409
        assert excinfo.value.existing_hostname == "ghost-all-abc123.tunnel.snodo.dev"


class TestTunnelRunErrors:
    """Test error paths in _run_tunnel."""

    def test_no_cloudflared_shows_install_instructions(self):
        """Missing cloudflared prints install help and returns 1."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from snodo.cli.commands.serve_cmd import _run_tunnel

        mock_protocol = MagicMock()
        args = SimpleNamespace(
            protocol=".snodo/protocol.yml", mode=None,
            transport="streamable-http", port=8000, rotate=False,
        )

        with patch("snodo.cli.commands.serve_cmd._check_cloudflared", return_value=False):
            result = _run_tunnel(args, mock_protocol, ".snodo/protocol.yml")

        assert result == 1

    def test_no_snodo_account_shows_signup(self):
        """No API key shows signup URL and returns 1."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from snodo.cli.commands.serve_cmd import _run_tunnel

        mock_protocol = MagicMock()
        args = SimpleNamespace(
            protocol=".snodo/protocol.yml", mode=None,
            transport="streamable-http", port=8000, rotate=False,
        )

        with patch("snodo.cli.commands.serve_cmd._check_cloudflared", return_value=True):
            with patch("snodo.cli.commands.serve_cmd._get_snodo_api_key", return_value=""):
                result = _run_tunnel(args, mock_protocol, ".snodo/protocol.yml")

        assert result == 1

    def test_rotate_is_no_op(self):
        """--rotate is now a no-op that prints a message."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from snodo.cli.commands.serve_cmd import _run_tunnel

        mock_protocol = MagicMock()
        args = SimpleNamespace(
            protocol=".snodo/protocol.yml", mode=None,
            transport="streamable-http", port=8000, rotate=True,
        )

        with patch("snodo.cli.commands.serve_cmd._check_cloudflared", return_value=True):
            with patch("snodo.cli.commands.serve_cmd._get_snodo_api_key", return_value="key123"):
                with patch("snodo.cli.commands.serve_cmd._load_tunnel_config", return_value={}):
                    result = _run_tunnel(args, mock_protocol, ".snodo/protocol.yml")

        assert result == 0

    def test_first_run_provisions_and_starts_services(self):
        """First run provisions tunnel, saves config, starts subprocesses."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from snodo.cli.commands.serve_cmd import _run_tunnel

        mock_protocol = MagicMock()
        args = SimpleNamespace(
            protocol=".snodo/protocol.yml", mode=None,
            transport="streamable-http", port=9090, rotate=False,
        )

        provisioned = {
            "hostname": "myproject-prod-a1b2c3.tunnel.snodo.dev",
            "tunnel_token": "tok_xxx",
            "client_id": "client_abc.access",
            "client_secret": "s3cr3t",
        }

        mock_sub = MagicMock()
        mock_sub.pid = 12345
        mock_sub.poll.return_value = None
        mock_sub.stderr.readline.return_value = "Registered tunnel connection\n"

        with patch("snodo.cli.commands.serve_cmd._check_cloudflared", return_value=True):
            with patch("snodo.cli.commands.serve_cmd._get_snodo_api_key", return_value="key123"):
                with patch("snodo.cli.commands.serve_cmd._load_tunnel_config", return_value={}):
                    with patch("snodo.cli.commands.serve_cmd._provision_tunnel", return_value=provisioned):
                        with patch("snodo.cli.commands.serve_cmd._save_tunnel_config"):
                            with patch("httpx.get", return_value=MagicMock(status_code=200)):
                                with patch("snodo.cli.commands.serve_cmd.subprocess.Popen", return_value=mock_sub):
                                    with patch("snodo.cli.commands.serve_cmd.signal.signal"):
                                        # Injected clock: the 2s uvicorn bind
                                        # wait and the 0.1s poll both advance
                                        # instantly instead of sleeping.
                                        with patch("snodo.cli.commands.serve_cmd.time.sleep", lambda _: None):
                                            # Return immediately so the function doesn't block
                                            mock_sub.wait.return_value = 0
                                            result = _run_tunnel(args, mock_protocol, ".snodo/protocol.yml")

        assert result == 0

    def test_subsequent_run_uses_stored_config(self):
        """Subsequent run with tunnel.json skips provisioning."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from snodo.cli.commands.serve_cmd import _run_tunnel

        mock_protocol = MagicMock()
        args = SimpleNamespace(
            protocol=".snodo/protocol.yml", mode=None,
            transport="streamable-http", port=8000, rotate=False,
        )

        stored = {
            "hostname": "existing.tunnel.snodo.dev",
            "tunnel_token": "tok_existing",
            "client_id": "client_old.access",
            "created_at": "2026-01-01T00:00:00Z",
        }

        mock_sub = MagicMock()
        mock_sub.pid = 12345
        mock_sub.poll.return_value = None
        mock_sub.stderr.readline.return_value = "Registered tunnel connection\n"

        with patch("snodo.cli.commands.serve_cmd._check_cloudflared", return_value=True):
            with patch("snodo.cli.commands.serve_cmd._get_snodo_api_key", return_value="key123"):
                with patch("snodo.cli.commands.serve_cmd._load_tunnel_config", return_value=stored):
                    with patch("snodo.cli.commands.serve_cmd._provision_tunnel") as mock_provision:
                        with patch("httpx.get", return_value=MagicMock(status_code=200)):
                            with patch("snodo.cli.commands.serve_cmd.subprocess.Popen", return_value=mock_sub):
                                with patch("snodo.cli.commands.serve_cmd.signal.signal"):
                                    # Injected clock: the 2s uvicorn bind wait
                                    # advances instantly instead of sleeping.
                                    with patch("snodo.cli.commands.serve_cmd.time.sleep", lambda _: None):
                                        mock_sub.wait.return_value = 0
                                        result = _run_tunnel(args, mock_protocol, ".snodo/protocol.yml")

        assert result == 0
        mock_provision.assert_not_called()  # No provisioning on subsequent run

    def test_rotate_is_no_op_ignores_tunnel_config(self):
        """--rotate is a no-op regardless of existing tunnel config."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from snodo.cli.commands.serve_cmd import _run_tunnel

        mock_protocol = MagicMock()
        args = SimpleNamespace(
            protocol=".snodo/protocol.yml", mode=None,
            transport="streamable-http", port=8000, rotate=True,
        )

        stored = {
            "hostname": "existing.tunnel.snodo.dev",
            "tunnel_token": "tok_old",
        }

        with patch("snodo.cli.commands.serve_cmd._check_cloudflared", return_value=True):
            with patch("snodo.cli.commands.serve_cmd._get_snodo_api_key", return_value="key123"):
                with patch("snodo.cli.commands.serve_cmd._load_tunnel_config", return_value=stored):
                    with patch("snodo.cli.commands.serve_cmd._rotate_tunnel_token") as mock_rotate:
                        with patch("snodo.cli.commands.serve_cmd._save_tunnel_config") as mock_save:
                            result = _run_tunnel(args, mock_protocol, ".snodo/protocol.yml")

        assert result == 0
        mock_rotate.assert_not_called()
        mock_save.assert_not_called()

    def test_non_auth_provision_failure_does_not_blame_api_key(self, capsys):
        """A 400 from provisioning reports the response and does NOT tell
        the user to re-run snodo cloud connect."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from snodo.cli.commands.serve_cmd import TunnelAPIError, _run_tunnel

        mock_protocol = MagicMock()
        args = SimpleNamespace(
            protocol=".snodo/protocol.yml", mode=None,
            transport="streamable-http", port=8000, rotate=False, delete=False,
        )

        err = TunnelAPIError(
            "Tunnel provisioning failed: POST https://app.snodo.dev/tunnel/provision "
            "returned HTTP 400: Missing or invalid field: project_path",
            status_code=400,
        )

        with patch("snodo.cli.commands.serve_cmd._check_cloudflared", return_value=True):
            with patch("snodo.cli.commands.serve_cmd._get_snodo_api_key", return_value="key123"):
                with patch("snodo.cli.commands.serve_cmd._load_tunnel_config", return_value={}):
                    with patch("snodo.cli.commands.serve_cmd._provision_tunnel", side_effect=err):
                        result = _run_tunnel(args, mock_protocol, ".snodo/protocol.yml")

        assert result == 1
        stderr_text = capsys.readouterr().err
        assert "HTTP 400" in stderr_text
        assert "project_path" in stderr_text
        assert "not an authentication error" in stderr_text
        assert "Re-run: snodo cloud connect" not in stderr_text

    def test_auth_provision_failure_advises_reconnect(self, capsys):
        """A 401 keeps the reconnect advice."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from snodo.cli.commands.serve_cmd import TunnelAPIError, _run_tunnel

        mock_protocol = MagicMock()
        args = SimpleNamespace(
            protocol=".snodo/protocol.yml", mode=None,
            transport="streamable-http", port=8000, rotate=False, delete=False,
        )

        err = TunnelAPIError(
            "Tunnel provisioning failed: POST https://app.snodo.dev/tunnel/provision "
            "returned HTTP 401: unauthorized",
            status_code=401,
        )

        with patch("snodo.cli.commands.serve_cmd._check_cloudflared", return_value=True):
            with patch("snodo.cli.commands.serve_cmd._get_snodo_api_key", return_value="key123"):
                with patch("snodo.cli.commands.serve_cmd._load_tunnel_config", return_value={}):
                    with patch("snodo.cli.commands.serve_cmd._provision_tunnel", side_effect=err):
                        result = _run_tunnel(args, mock_protocol, ".snodo/protocol.yml")

        assert result == 1
        stderr_text = capsys.readouterr().err
        assert "HTTP 401" in stderr_text
        assert "cloud connect" in stderr_text

    # -- 409 conflict at the run level ---------------------------------

    def test_conflict_surfaces_blocking_hostname_actionably(self, capsys):
        """A 409 prints the existing hostname and a pasteable replace
        command — the operator can act without parsing the body."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from snodo.cli.commands.serve_cmd import TunnelAPIError, _run_tunnel

        mock_protocol = MagicMock()
        args = SimpleNamespace(
            protocol=".snodo/protocol.yml", mode=None,
            transport="streamable-http", port=8000, rotate=False, delete=False,
        )

        err = TunnelAPIError(
            "Tunnel provisioning failed: POST https://app.snodo.dev/tunnel/provision "
            "returned HTTP 409: {\"hostname\": \"ghost-all-abc123.tunnel.snodo.dev\"}",
            status_code=409,
            existing_hostname="ghost-all-abc123.tunnel.snodo.dev",
        )

        with patch("snodo.cli.commands.serve_cmd._check_cloudflared", return_value=True):
            with patch("snodo.cli.commands.serve_cmd._get_snodo_api_key", return_value="key123"):
                with patch("snodo.cli.commands.serve_cmd._load_tunnel_config", return_value={}):
                    with patch("snodo.cli.commands.serve_cmd._provision_tunnel", side_effect=err):
                        result = _run_tunnel(args, mock_protocol, ".snodo/protocol.yml")

        assert result == 1
        stderr_text = capsys.readouterr().err
        assert "Blocking tunnel: ghost-all-abc123.tunnel.snodo.dev" in stderr_text
        assert ("snodo serve --tunnel --delete --hostname "
                "ghost-all-abc123.tunnel.snodo.dev") in stderr_text
        assert "cloud connect" not in stderr_text

    def test_conflict_without_named_host_says_so(self, capsys):
        """A 409 whose body names no hostname must not print a command with
        a hole in it."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from snodo.cli.commands.serve_cmd import TunnelAPIError, _run_tunnel

        mock_protocol = MagicMock()
        args = SimpleNamespace(
            protocol=".snodo/protocol.yml", mode=None,
            transport="streamable-http", port=8000, rotate=False, delete=False,
        )

        err = TunnelAPIError("HTTP 409", status_code=409, existing_hostname=None)

        with patch("snodo.cli.commands.serve_cmd._check_cloudflared", return_value=True):
            with patch("snodo.cli.commands.serve_cmd._get_snodo_api_key", return_value="key123"):
                with patch("snodo.cli.commands.serve_cmd._load_tunnel_config", return_value={}):
                    with patch("snodo.cli.commands.serve_cmd._provision_tunnel", side_effect=err):
                        result = _run_tunnel(args, mock_protocol, ".snodo/protocol.yml")

        assert result == 1
        stderr_text = capsys.readouterr().err
        assert "did not name the existing tunnel" in stderr_text
        assert "--delete --hostname" not in stderr_text

    # -- --delete by hostname the local config never recorded ----------

    def test_delete_can_reach_tunnel_not_in_local_config(self, capsys, monkeypatch):
        """--delete --hostname addresses the cloud-held tunnel even when
        tunnel.json is empty — the old 'No tunnel configured' dead end."""
        import httpx

        from snodo.cli.commands import serve_cmd
        from snodo.cli.commands.serve_cmd import _handle_tunnel_delete

        captured = {}

        def fake_delete(url, **kwargs):
            captured["url"] = url
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "ok"
            return resp

        monkeypatch.setattr(httpx, "delete", fake_delete)
        monkeypatch.setattr(serve_cmd, "_get_cloud_tunnel_api_url",
                            lambda: "https://tunnel.snodo.example.test")

        result = _handle_tunnel_delete(
            "/nonexistent-project-root", {}, "key123",
            hostname="ghost-all-abc123.tunnel.snodo.dev",
        )

        assert result == 0
        assert captured["url"] == (
            "https://tunnel.snodo.example.test/tunnel/ghost-all-abc123.tunnel.snodo.dev"
        )
        out = capsys.readouterr()
        assert "No tunnel configured" not in out.err
        assert "ghost-all-abc123.tunnel.snodo.dev" in out.out

    def test_run_tunnel_wires_hostname_into_delete(self, capsys):
        """args.hostname reaches _handle_tunnel_delete via _run_tunnel."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from snodo.cli.commands.serve_cmd import _run_tunnel

        mock_protocol = MagicMock()
        args = SimpleNamespace(
            protocol=".snodo/protocol.yml", mode=None,
            transport="streamable-http", port=8000,
            rotate=False, delete=True, hostname="ghost-all-abc123.tunnel.snodo.dev",
        )

        with patch("snodo.cli.commands.serve_cmd._check_cloudflared", return_value=True):
            with patch("snodo.cli.commands.serve_cmd._get_snodo_api_key", return_value="key123"):
                with patch("snodo.cli.commands.serve_cmd._load_tunnel_config", return_value={}):
                    with patch("snodo.cli.commands.serve_cmd._handle_tunnel_delete",
                               return_value=0) as mock_del:
                        result = _run_tunnel(args, mock_protocol, ".snodo/protocol.yml")

        assert result == 0
        mock_del.assert_called_once_with(
            mock_del.call_args.args[0], {}, "key123",
            "ghost-all-abc123.tunnel.snodo.dev",
        )

    def test_delete_without_local_record_advises_hostname(self, capsys):
        """No local config and no --hostname: point at the by-name escape."""
        from snodo.cli.commands.serve_cmd import _handle_tunnel_delete

        result = _handle_tunnel_delete("/nonexistent-project-root", {}, "key123")

        assert result == 1
        stderr_text = capsys.readouterr().err
        assert "No tunnel configured" in stderr_text
        assert "--delete --hostname <hostname>" in stderr_text

    def test_explicit_hostname_delete_preserves_unrelated_local_record(self, tmp_path, monkeypatch):
        """Removing a named tunnel that is not the locally recorded one
        leaves tunnel.json alone."""
        import httpx

        from snodo.cli.commands import serve_cmd
        from snodo.cli.commands.serve_cmd import _handle_tunnel_delete

        tunnel_file = tmp_path / ".snodo" / "tunnel.json"
        tunnel_file.parent.mkdir(parents=True)
        tunnel_file.write_text('{"hostname": "mine-all-aaa111.tunnel.snodo.dev"}')

        resp = MagicMock()
        resp.status_code = 200
        resp.text = "ok"
        monkeypatch.setattr(httpx, "delete", lambda url, **kw: resp)
        monkeypatch.setattr(serve_cmd, "_get_cloud_tunnel_api_url",
                            lambda: "https://tunnel.snodo.example.test")

        local = {"hostname": "mine-all-aaa111.tunnel.snodo.dev"}
        result = _handle_tunnel_delete(
            str(tmp_path), local, "key123",
            hostname="ghost-all-abc123.tunnel.snodo.dev",
        )

        assert result == 0
        assert tunnel_file.exists()  # local record names a different tunnel

        # Deleting the locally recorded hostname still clears it.
        result = _handle_tunnel_delete(str(tmp_path), local, "key123")
        assert result == 0
        assert not tunnel_file.exists()

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

    def test_tool_call_logs_active_mode(self, audited_server, audit_log, project_dir):
        """A tool invocation records the active mode (mode attribution)."""
        from snodo.infrastructure.state import ProjectState, write_state
        write_state(project_dir, ProjectState(current_mode="reviewer"))

        audited_server.call_tool("read_file", {"path": "README.md"})
        events = audit_log.get_history(event_type="tool_call")
        assert len(events) == 1
        assert events[0].data["mode"] == "reviewer"

    def test_tool_call_logs_pinned_mode(self, project_dir, audit_log):
        """A single-mode server records its pinned mode, not state.json."""
        from snodo.infrastructure.state import ProjectState, write_state
        write_state(project_dir, ProjectState(current_mode="reviewer"))

        protocol = Protocol(**MINIMAL_PROTOCOL_DATA)
        server = ProtocolMCPServer(protocol, project_dir, mode_id="producer", audit_log=audit_log)
        server.call_tool("read_file", {"path": "README.md"})
        events = audit_log.get_history(event_type="tool_call")
        assert len(events) == 1
        assert events[0].data["mode"] == "producer"

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
