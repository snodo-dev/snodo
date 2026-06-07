"""Tests for ReconManager and ReconToolHandler.

FILE: tests/recon/test_recon.py
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from snodo.recon import ReconManager, ReconState, ReconResult, ReconError


@pytest.fixture
def project_with_snodo():
    """Create a temp project with .snodo dir."""
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp) / "myproject"
        project_root.mkdir()
        (project_root / ".snodo").mkdir()
        yield str(project_root)


@pytest.fixture
def recon_mgr(project_with_snodo):
    mgr = ReconManager(project_with_snodo)
    yield mgr
    mgr.shutdown()


# ------------------------------------------------------------------#
# ReconManager tests
# ------------------------------------------------------------------#

class TestReconManagerConstruction:
    def test_creates_recons_dir(self, project_with_snodo):
        mgr = ReconManager(project_with_snodo)
        recons_dir = Path(project_with_snodo) / ".snodo" / "recons"
        assert recons_dir.is_dir()

    def test_fails_without_snodo_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(ValueError, match="Not a snodo project"):
                ReconManager(tmp)


class TestReconManagerSubmit:
    def test_submit_creates_state_and_returns_id(self, recon_mgr):
        recon_id = recon_mgr.submit("What does this code do?", ["./"])

        assert recon_id.startswith("rec_")
        recon_dir = Path(recon_mgr.recons_dir) / recon_id
        assert recon_dir.is_dir()

        state_path = recon_dir / "state.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert state["recon_id"] == recon_id
        assert state["query"] == "What does this code do?"
        assert state["paths"] == ["./"]
        assert state["agents"] == ["default"]
        assert state["status"] == "running"
        assert "created_at" in state

    def test_submit_custom_agents(self, recon_mgr):
        agents = ["gpt-4", "gemini/gemini-2.0-flash-exp"]
        recon_id = recon_mgr.submit("analyze", ["src/"], agents=agents)

        recon_dir = Path(recon_mgr.recons_dir) / recon_id
        state = json.loads((recon_dir / "state.json").read_text())
        assert state["agents"] == agents


class TestReconManagerGetStatus:
    def test_get_status_running(self, recon_mgr):
        recon_id = recon_mgr.submit("query", ["./"])
        status = recon_mgr.get_status(recon_id)
        assert status["status"] == "running"
        assert status["recon_id"] == recon_id

    def test_get_status_not_found(self, recon_mgr):
        with pytest.raises(ReconError, match="not found"):
            recon_mgr.get_status("rec_nonexistent")


class TestReconManagerGetResults:
    def test_get_results_before_complete_raises(self, recon_mgr):
        recon_id = recon_mgr.submit("query", ["./"])
        with pytest.raises(ReconError, match="not complete"):
            recon_mgr.get_results(recon_id)


class TestReconManagerListRecons:
    def test_list_empty(self, recon_mgr):
        recons = recon_mgr.list_recons()
        assert recons == []

    def test_list_returns_submitted_recons(self, recon_mgr):
        recon_mgr.submit("first query", ["./"])
        recon_mgr.submit("second query", ["src/"])

        recons = recon_mgr.list_recons()
        assert len(recons) == 2
        assert recons[0]["query"] == "second query"
        assert recons[1]["query"] == "first query"

    def test_list_respects_limit(self, recon_mgr):
        for i in range(5):
            recon_mgr.submit(f"query {i}", ["./"])
        recons = recon_mgr.list_recons(limit=2)
        assert len(recons) == 2


# ------------------------------------------------------------------#
# ReconModel tests
# ------------------------------------------------------------------#

class TestReconState:
    def test_creation(self):
        state = ReconState(
            recon_id="rec_test",
            query="q",
            paths=["./"],
            agents=["default"],
            status="running",
            created_at=0.0,
        )
        assert state.recon_id == "rec_test"
        assert state.status == "running"
        assert state.completed_at is None


class TestReconResult:
    def test_success_result(self):
        result = ReconResult(
            agent="default",
            model="gpt-4",
            result="The codebase uses FastAPI.",
        )
        assert result.error is None
        assert result.result == "The codebase uses FastAPI."

    def test_error_result(self):
        result = ReconResult(
            agent="openai",
            model="gpt-4",
            result="",
            error="API key missing",
        )
        assert result.error == "API key missing"
        assert result.result == ""


# ------------------------------------------------------------------#
# ReconToolHandler tests
# ------------------------------------------------------------------#

class TestReconToolHandler:
    def test_handle_recon_requires_query(self, project_with_snodo):
        from snodo.mcp.recon_handlers import ReconToolHandler
        from snodo.mcp.server import MCPError

        handler = ReconToolHandler(project_with_snodo)
        with pytest.raises(MCPError, match="query"):
            handler.handle_recon({})

    def test_handle_recon_requires_paths(self, project_with_snodo):
        from snodo.mcp.recon_handlers import ReconToolHandler
        from snodo.mcp.server import MCPError

        handler = ReconToolHandler(project_with_snodo)
        with pytest.raises(MCPError, match="paths"):
            handler.handle_recon({"query": "q", "paths": []})

    def test_handle_recon_returns_id(self, project_with_snodo, recon_mgr):
        from snodo.mcp.recon_handlers import ReconToolHandler

        handler = ReconToolHandler(project_with_snodo)
        result = handler.handle_recon({
            "query": "What is this project?",
            "paths": ["./"],
            "agents": ["default"],
        })

        assert result["recon_id"].startswith("rec_")
        assert result["status"] == "running"
        assert "default" in result["agents"]

    def test_handle_get_recon_status_requires_id(self, project_with_snodo):
        from snodo.mcp.recon_handlers import ReconToolHandler
        from snodo.mcp.server import MCPError

        handler = ReconToolHandler(project_with_snodo)
        with pytest.raises(MCPError, match="recon_id"):
            handler.handle_get_recon_status({})

    def test_handle_get_recon_results_requires_id(self, project_with_snodo):
        from snodo.mcp.recon_handlers import ReconToolHandler
        from snodo.mcp.server import MCPError

        handler = ReconToolHandler(project_with_snodo)
        with pytest.raises(MCPError, match="recon_id"):
            handler.handle_get_recon_results({})

    def test_handle_get_recon_status_not_found(self, project_with_snodo):
        from snodo.mcp.recon_handlers import ReconToolHandler
        from snodo.mcp.server import MCPError

        handler = ReconToolHandler(project_with_snodo)
        with pytest.raises(MCPError):
            handler.handle_get_recon_status({"recon_id": "rec_nonexistent"})


# ------------------------------------------------------------------#
# Resolve agent model tests
# ------------------------------------------------------------------#

class TestResolveAgentModel:
    def test_default_resolves_to_configured_model(self):
        from snodo.recon import _resolve_agent_model

        with patch("snodo.cli.config.ConfigManager") as MockCM:
            MockCM.return_value.get_model.return_value = "gpt-4"
            result = _resolve_agent_model("default")
            assert result == "gpt-4"

    def test_named_agent_passes_through(self):
        from snodo.recon import _resolve_agent_model
        result = _resolve_agent_model("gemini/gemini-2.0-flash-exp")
        assert result == "gemini/gemini-2.0-flash-exp"


# ------------------------------------------------------------------#
# Read-only tool surface tests
# ------------------------------------------------------------------#

class TestReadOnlyTools:
    def test_read_file_tool_surface(self, tmp_path):
        from snodo.recon import _READ_FILE_TOOL, _LIST_FILES_TOOL

        assert _READ_FILE_TOOL["function"]["name"] == "read_file"
        assert _LIST_FILES_TOOL["function"]["name"] == "list_files"

        # Verify no write tools in the surface
        tool_names = {
            _READ_FILE_TOOL["function"]["name"],
            _LIST_FILES_TOOL["function"]["name"],
        }
        assert "write_file" not in tool_names
        assert "delete_file" not in tool_names
