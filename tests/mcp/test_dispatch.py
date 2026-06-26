import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
import tempfile
import shutil
import subprocess

from snodo.compiler.models import Protocol
from snodo.mcp.server import ProtocolMCPServer, MCPError


FULL_PROTOCOL_DATA = {
    "protocol_id": "test",
    "name": "Full Test",
    "version": "1.0.0",
    "modes": [
        {
            "mode_id": "full",
            "name": "Full",
            "tools": ["edit", "decide", "dispatch"],
            "validators": ["security"],
        },
    ],
    "validators": [
        {"validator_id": "security", "validator_type": "security"},
    ],
    "disagreement_policy": "unanimous",
    "initial_mode": "full",
}


@pytest.fixture
def protocol():
    return Protocol(**FULL_PROTOCOL_DATA)


@pytest.fixture
def project_dir():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=d, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=d, capture_output=True, check=True
    )
    (Path(d) / "README.md").write_text("test")
    subprocess.run(["git", "add", "."], cwd=d, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=d, capture_output=True, check=True
    )
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def server(protocol, project_dir):
    return ProtocolMCPServer(protocol, project_dir)


def test_previously_dispatched_tools_route_to_handlers(server):
    """Verify every previously-dispatched tool routes to the correct bound method."""
    dispatch = server._dispatch

    # Core/Server tool handlers
    assert dispatch["validate_task"] == server._handle_validate_task
    assert dispatch["dispatch_task"] == server._handle_dispatch_task
    assert dispatch["retry_job"] == server._handle_retry_job

    # Job tool handlers
    assert dispatch["get_job_status"] == server._job_handler.handle_get_job_status
    assert dispatch["list_jobs"] == server._job_handler.handle_list_jobs
    assert dispatch["get_job_logs"] == server._job_handler.handle_get_job_logs

    # Model tool handlers
    assert dispatch["list_models"] == server._model_handler.handle_list_models
    assert dispatch["resolve_model"] == server._model_handler.handle_resolve_model

    # Decision tool handlers
    assert dispatch["propose_adjudicate"] == server._decision_handler.handle_propose_adjudicate
    assert dispatch["propose_set_model"] == server._decision_handler.handle_propose_set_model

    # Recon tool handlers
    assert dispatch["recon"] == server._recon_handler.handle_recon
    assert dispatch["get_recon_status"] == server._recon_handler.handle_get_recon_status
    assert dispatch["get_recon_results"] == server._recon_handler.handle_get_recon_results


def test_unknown_tool_raises_mcp_error(server):
    """Verify that calling an unknown tool raises MCPError."""
    with pytest.raises(MCPError, match="Unknown tool: non_existent_tool"):
        server.call_tool("non_existent_tool", {})


def test_collision_detection_raises_at_init(protocol, project_dir):
    """Verify that duplicate tool registration raises ValueError during initialization."""
    # Mock one of the handlers' tool_handlers to return a colliding key
    with patch("snodo.mcp.model_handlers.ModelToolHandler.tool_handlers") as mock_tool_handlers:
        # Let's say list_jobs is returned by ModelToolHandler too, causing collision with JobToolHandler
        mock_tool_handlers.return_value = {
            "list_jobs": lambda x: {}
        }
        with pytest.raises(ValueError, match="Duplicate tool handler registered for tool: list_jobs"):
            ProtocolMCPServer(protocol, project_dir)
