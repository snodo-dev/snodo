"""Characterization tests for snodo/mcp/recon_handlers.py (14% → ~95%).

Pins ReconToolHandler.handle_recon, handle_get_recon_status,
handle_get_recon_results — all success + guard branches.
"""

import pytest
from unittest.mock import patch

from snodo.mcp.recon_handlers import ReconToolHandler
from snodo.mcp.server import MCPError
from snodo.recon import ReconError


def _handler():
    return ReconToolHandler("/tmp/project")


# ---------------------------------------------------------------------------
# handle_recon — guard branches
# ---------------------------------------------------------------------------

class TestHandleRecon:
    def test_missing_query_raises(self):
        with pytest.raises(MCPError, match="requires query"):
            _handler().handle_recon({})

    def test_empty_query_raises(self):
        with pytest.raises(MCPError, match="requires query"):
            _handler().handle_recon({"query": ""})

    def test_empty_paths_raises(self):
        with pytest.raises(MCPError, match="requires paths"):
            _handler().handle_recon({"query": "find auth", "paths": []})

    def test_paths_not_list_raises(self):
        with pytest.raises(MCPError, match="requires paths"):
            _handler().handle_recon({"query": "find auth", "paths": "./"})

    def test_success_returns_recon_id(self):
        handler = _handler()
        with patch("snodo.config.ConfigManager") as MockCM, \
             patch("snodo.recon.ReconManager") as MockRM, \
             patch("snodo.recon.resolve_recon_agents") as mock_rra:
            MockCM.return_value.load.return_value = {
                "llm": {"recon": {"models": ["default"], "num_agents": 1}}
            }
            mock_rra.return_value = ["default"]
            MockRM.return_value.submit.return_value = "recon-abc123"
            result = handler.handle_recon({"query": "find auth code", "paths": ["./"]})
        assert result["recon_id"] == "recon-abc123"
        assert result["status"] == "running"
        assert result["query"] == "find auth code"
        assert "agents" in result

    def test_recon_error_wrapped_as_mcp_error(self):
        handler = _handler()
        with patch("snodo.config.ConfigManager") as MockCM, \
             patch("snodo.recon.ReconManager") as MockRM, \
             patch("snodo.recon.resolve_recon_agents") as mock_rra:
            MockCM.return_value.load.return_value = {}
            mock_rra.return_value = ["default"]
            MockRM.return_value.submit.side_effect = ReconError("submit failed")
            with pytest.raises(MCPError, match="submit failed"):
                handler.handle_recon({"query": "find auth", "paths": ["./"]})

    def test_explicit_agents_passed_through(self):
        """agents list in arguments is forwarded to resolve_recon_agents."""
        handler = _handler()
        with patch("snodo.config.ConfigManager") as MockCM, \
             patch("snodo.recon.ReconManager") as MockRM, \
             patch("snodo.recon.resolve_recon_agents") as mock_rra:
            MockCM.return_value.load.return_value = {}
            mock_rra.return_value = ["claude-sonnet", "gemini"]
            MockRM.return_value.submit.return_value = "recon-xy"
            handler.handle_recon({
                "query": "what is X",
                "paths": ["./src"],
                "agents": ["claude-sonnet", "gemini"],
            })
        # explicit_agents kwarg should have been non-None
        call_kwargs = mock_rra.call_args[1]
        assert call_kwargs["explicit_agents"] == ["claude-sonnet", "gemini"]

    def test_num_agents_forwarded(self):
        handler = _handler()
        with patch("snodo.config.ConfigManager") as MockCM, \
             patch("snodo.recon.ReconManager") as MockRM, \
             patch("snodo.recon.resolve_recon_agents") as mock_rra:
            MockCM.return_value.load.return_value = {}
            mock_rra.return_value = ["m1", "m2", "m3"]
            MockRM.return_value.submit.return_value = "recon-3"
            handler.handle_recon({"query": "q", "paths": ["./"], "num_agents": 3})
        call_kwargs = mock_rra.call_args[1]
        assert call_kwargs["requested_n"] == 3


# ---------------------------------------------------------------------------
# handle_get_recon_status — guard + success + error
# ---------------------------------------------------------------------------

class TestHandleGetReconStatus:
    def test_missing_recon_id_raises(self):
        with pytest.raises(MCPError, match="requires recon_id"):
            _handler().handle_get_recon_status({})

    def test_empty_recon_id_raises(self):
        with pytest.raises(MCPError, match="requires recon_id"):
            _handler().handle_get_recon_status({"recon_id": ""})

    def test_success_returns_status_dict(self):
        handler = _handler()
        with patch("snodo.recon.ReconManager") as MockRM:
            MockRM.return_value.get_status.return_value = {
                "status": "running",
                "recon_id": "recon-001",
                "agents": 2,
            }
            result = handler.handle_get_recon_status({"recon_id": "recon-001"})
        assert result["status"] == "running"
        MockRM.return_value.get_status.assert_called_once_with("recon-001")

    def test_recon_error_wrapped(self):
        handler = _handler()
        with patch("snodo.recon.ReconManager") as MockRM:
            MockRM.return_value.get_status.side_effect = ReconError("not found")
            with pytest.raises(MCPError, match="not found"):
                handler.handle_get_recon_status({"recon_id": "bad-id"})


# ---------------------------------------------------------------------------
# handle_get_recon_results — guard + success + error
# ---------------------------------------------------------------------------

class TestHandleGetReconResults:
    def test_missing_recon_id_raises(self):
        with pytest.raises(MCPError, match="requires recon_id"):
            _handler().handle_get_recon_results({})

    def test_empty_recon_id_raises(self):
        with pytest.raises(MCPError, match="requires recon_id"):
            _handler().handle_get_recon_results({"recon_id": ""})

    def test_success_returns_results(self):
        handler = _handler()
        expected = {
            "recon_id": "recon-001",
            "status": "complete",
            "results": [{"agent": "default", "text": "found auth in auth.py"}],
        }
        with patch("snodo.recon.ReconManager") as MockRM:
            MockRM.return_value.get_results.return_value = expected
            result = handler.handle_get_recon_results({"recon_id": "recon-001"})
        assert result == expected

    def test_recon_error_wrapped(self):
        handler = _handler()
        with patch("snodo.recon.ReconManager") as MockRM:
            MockRM.return_value.get_results.side_effect = ReconError("not complete yet")
            with pytest.raises(MCPError, match="not complete yet"):
                handler.handle_get_recon_results({"recon_id": "recon-002"})
