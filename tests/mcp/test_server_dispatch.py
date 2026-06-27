"""Characterization tests for missing ProtocolMCPServer dispatch branches.

Covers server.py lines:
  116-117  _resolve_provider exception → returns None
  201-215  call_tool dispatch for handler-backed tools
  292, 296 _dispatch_tool error branches
  327-329  _handle_validate_task test-runner exception path
  393, 413, 426  _handle_dispatch_task mode_id / consumed / coding_model
  435-471  _handle_retry_job full path
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from snodo.compiler.models import Protocol
from snodo.mcp.server import MCPError, ProtocolMCPServer


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# Protocol with edit (→ list_models, resolve_model, recon, get_recon_status,
# get_recon_results) + decide (→ propose_adjudicate, propose_set_model) +
# dispatch (→ dispatch_task, get_job_status, list_jobs, get_job_logs, retry_job)
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


@pytest.fixture
def server_with_mode(protocol, project_dir):
    return ProtocolMCPServer(protocol, project_dir, mode_id="full")


def _issue_token(server, task_id="t1"):
    """Issue a token so mutating tools pass WF1."""
    server._handle_validate_task({"task_id": task_id})


# ---------------------------------------------------------------------------
# _resolve_provider exception → returns None (lines 116-117)
# ---------------------------------------------------------------------------

class TestResolveProvider:
    def test_detect_provider_exception_returns_none(self, protocol, project_dir):
        """Lines 116-117: if detect_provider raises, _resolve_provider returns None."""
        with patch(
            "snodo.providers.registry.detect_provider",
            side_effect=RuntimeError("no provider"),
        ):
            srv = ProtocolMCPServer(protocol, project_dir)
        # PrMCP still initialised (with provider=None), server did not raise
        assert srv.pr is not None

    def test_resolve_provider_direct_exception(self, protocol, project_dir):
        """Call _resolve_provider directly with a patched detect_provider that raises."""
        srv = ProtocolMCPServer(protocol, project_dir)
        with patch(
            "snodo.providers.registry.detect_provider",
            side_effect=Exception("registry gone"),
        ):
            result = srv._resolve_provider()
        assert result is None


# ---------------------------------------------------------------------------
# call_tool dispatch: handler-backed meta-tools (lines 201-215)
# ---------------------------------------------------------------------------

class TestCallToolDispatchBranches:
    def test_list_models_routed(self, server):
        mock_result = {"models": []}
        server._model_handler.handle_list_models = MagicMock(return_value=mock_result)
        result = server.call_tool("list_models", {})
        server._model_handler.handle_list_models.assert_called_once_with({})
        assert result == mock_result

    def test_resolve_model_routed(self, server):
        mock_result = {"status": "exact_match", "model": "claude-sonnet"}
        server._model_handler.handle_resolve_model = MagicMock(return_value=mock_result)
        result = server.call_tool("resolve_model", {"query": "sonnet"})
        server._model_handler.handle_resolve_model.assert_called_once()
        assert result["status"] == "exact_match"

    def test_propose_adjudicate_routed(self, server):
        mock_result = {"status": "pending", "task_id": "t1", "instruction": "...", "proposal": {}}
        server._decision_handler.handle_propose_adjudicate = MagicMock(return_value=mock_result)
        result = server.call_tool("propose_adjudicate", {
            "task_id": "t1",
            "validator_id": "sec",
            "decision": "proceed",
            "justification": "ok",
        })
        server._decision_handler.handle_propose_adjudicate.assert_called_once()
        assert result["status"] == "pending"

    def test_propose_set_model_routed(self, server):
        mock_result = {"status": "pending", "task_id": "t1", "instruction": "...", "proposal": {}}
        server._decision_handler.handle_propose_set_model = MagicMock(return_value=mock_result)
        server.call_tool("propose_set_model", {
            "task_id": "t1",
            "proposed_model": "gpt-4o",
            "scope": "coder",
            "justification": "ok",
        })
        server._decision_handler.handle_propose_set_model.assert_called_once()

    def test_recon_routed(self, server):
        mock_result = {"recon_id": "r1", "status": "running", "agents": ["default"], "query": "q"}
        server._recon_handler.handle_recon = MagicMock(return_value=mock_result)
        result = server.call_tool("recon", {"query": "q", "paths": ["./"]})
        server._recon_handler.handle_recon.assert_called_once()
        assert result["recon_id"] == "r1"

    def test_get_recon_status_routed(self, server):
        mock_result = {"status": "running"}
        server._recon_handler.handle_get_recon_status = MagicMock(return_value=mock_result)
        server.call_tool("get_recon_status", {"recon_id": "r1"})
        server._recon_handler.handle_get_recon_status.assert_called_once()

    def test_get_recon_results_routed(self, server):
        mock_result = {"results": []}
        server._recon_handler.handle_get_recon_results = MagicMock(return_value=mock_result)
        server.call_tool("get_recon_results", {"recon_id": "r1"})
        server._recon_handler.handle_get_recon_results.assert_called_once()

    def test_retry_job_routed(self, server):
        """retry_job requires a token (requires_token=True)."""
        _issue_token(server)
        mock_result = {"status": "accepted", "job_id": "j2", "task_id": "t1", "description": "d"}
        server._handle_retry_job = MagicMock(return_value=mock_result)
        server.call_tool("retry_job", {"job_id": "j1"})
        server._handle_retry_job.assert_called_once()


# ---------------------------------------------------------------------------
# _dispatch_tool error branches (lines 292, 296)
# ---------------------------------------------------------------------------

class TestDispatchToolErrors:
    def test_no_backing_mcp_raises(self, server):
        """Schema with mcp=None → MCPError 'No backing MCP'."""
        schema = {"requires_token": False, "mcp": None, "method": "some_method"}
        with pytest.raises(MCPError, match="No backing MCP"):
            server._dispatch_tool("phantom_tool", schema, {})

    def test_method_not_found_raises(self, server):
        """Schema with valid mcp but nonexistent method → MCPError 'Method ... not found'."""
        schema = {
            "requires_token": False,
            "mcp": "workspace",
            "method": "nonexistent_method_xyz",
        }
        with pytest.raises(MCPError, match="Method nonexistent_method_xyz not found"):
            server._dispatch_tool("ghost_tool", schema, {})


# ---------------------------------------------------------------------------
# _handle_validate_task: test-runner exception path (lines 327-329)
# ---------------------------------------------------------------------------

class TestValidateTaskExceptionPath:
    def test_shell_run_tests_exception_becomes_warn(self, server):
        """Lines 328-329: when shell.run_tests raises, result is a 'warn' ValidatorResult."""
        with patch.object(
            server.shell, "run_tests", side_effect=RuntimeError("subprocess crash")
        ):
            result = server._handle_validate_task({"task_id": "t-crash"})
        warn_results = [
            r for r in result["results"] if r["severity"] == "warn"
        ]
        assert any("Test execution skipped" in r["justification"] for r in warn_results)

    def test_shell_run_tests_pass_appended_directly(self, server):
        """Line 327: non-blocker run_tests result appended directly (not wrapped)."""
        from snodo.core.interfaces import ValidatorResult
        pass_result = ValidatorResult(
            validator_id="test_runner", severity="pass", justification="all pass"
        )
        with patch.object(server.shell, "run_tests", return_value=pass_result):
            result = server._handle_validate_task({"task_id": "t-pass"})
        direct = [r for r in result["results"] if r["validator_id"] == "test_runner"]
        assert direct and direct[0]["severity"] == "pass"


# ---------------------------------------------------------------------------
# _handle_dispatch_task: mode_id, consumed, coding_model branches (393, 413, 426)
# ---------------------------------------------------------------------------

class TestDispatchTaskBranches:
    def _make_server_with_mode(self, protocol, project_dir):
        return ProtocolMCPServer(protocol, project_dir, mode_id="full")

    def test_mode_id_added_to_task_args(self, protocol, project_dir):
        """Line 393: mode_id set → task_args['mode'] = mode_id."""
        srv = self._make_server_with_mode(protocol, project_dir)
        captured = {}
        with patch("snodo.jobs.JobManager") as MockJM:
            MockJM.return_value.submit.side_effect = lambda args: (captured.update(args) or "j1")
            srv._handle_dispatch_task({"task_spec": "do something"})
        assert captured.get("mode") == "full"

    def test_no_token_to_consume_consumed_false(self, protocol, project_dir):
        """Line 413: no existing token → consumed=False, no token_consumed audit."""
        srv = ProtocolMCPServer(protocol, project_dir)
        audit_calls = []
        srv._audit_log = MagicMock()
        srv._audit_log.append_event.side_effect = lambda et, d: audit_calls.append(et)
        srv._validation_token = None  # no token

        with patch("snodo.jobs.JobManager") as MockJM:
            MockJM.return_value.submit.return_value = "j-notokenconsumed"
            srv._handle_dispatch_task({"task_spec": "spec without token"})
        # token_consumed event should NOT have been emitted
        assert "token_consumed" not in audit_calls

    def test_coding_model_added_to_result(self, protocol, project_dir):
        """Line 426: coding_model provided → included in return dict."""
        srv = ProtocolMCPServer(protocol, project_dir)
        with patch("snodo.jobs.JobManager") as MockJM:
            MockJM.return_value.submit.return_value = "j-model"
            result = srv._handle_dispatch_task({
                "task_spec": "spec",
                "coding_model": "gpt-4o-mini",
            })
        assert result["coding_model"] == "gpt-4o-mini"

    def test_no_coding_model_not_in_result(self, protocol, project_dir):
        """coding_model omitted → not in result dict."""
        srv = ProtocolMCPServer(protocol, project_dir)
        with patch("snodo.jobs.JobManager") as MockJM:
            MockJM.return_value.submit.return_value = "j-nomodel"
            result = srv._handle_dispatch_task({"task_spec": "spec"})
        assert "coding_model" not in result


# ---------------------------------------------------------------------------
# _handle_retry_job — full path (lines 435-471)
# ---------------------------------------------------------------------------

class TestHandleRetryJob:
    def test_missing_job_id_raises(self, server):
        with pytest.raises(MCPError, match="retry_job requires job_id"):
            server._handle_retry_job({})

    def test_missing_task_json_raises(self, server, project_dir):
        """If task.json doesn't exist → MCPError."""
        with patch("snodo.jobs.JobManager") as MockJM:
            MockJM.return_value._job_dir.return_value = Path(project_dir) / "nonexistent"
            with pytest.raises(MCPError, match="No task.json found"):
                server._handle_retry_job({"job_id": "j-missing"})

    def test_invalid_task_json_raises(self, server, project_dir, tmp_path):
        """If task.json contains invalid JSON → MCPError."""
        job_dir = tmp_path / "j-bad"
        job_dir.mkdir()
        (job_dir / "task.json").write_text("{{invalid")
        with patch("snodo.jobs.JobManager") as MockJM:
            MockJM.return_value._job_dir.return_value = job_dir
            with pytest.raises(MCPError, match="Error reading task.json"):
                server._handle_retry_job({"job_id": "j-bad"})

    def test_success_original_spec(self, server, tmp_path):
        """Successful retry with original spec."""
        job_dir = tmp_path / "j-ok"
        job_dir.mkdir()
        task_data = {"task_id": "original-task", "description": "implement feature X"}
        (job_dir / "task.json").write_text(json.dumps(task_data))
        with patch("snodo.jobs.JobManager") as MockJM:
            MockJM.return_value._job_dir.return_value = job_dir
            MockJM.return_value.submit.return_value = "j-retry-1"
            result = server._handle_retry_job({"job_id": "j-ok"})
        assert result["status"] == "accepted"
        assert result["job_id"] == "j-retry-1"
        assert result["task_id"] == "original-task"
        assert result["description"] == "implement feature X"

    def test_success_revised_spec(self, server, tmp_path):
        """Successful retry with revised spec overrides original."""
        job_dir = tmp_path / "j-rev"
        job_dir.mkdir()
        task_data = {"task_id": "orig-task", "description": "old spec"}
        (job_dir / "task.json").write_text(json.dumps(task_data))
        with patch("snodo.jobs.JobManager") as MockJM:
            MockJM.return_value._job_dir.return_value = job_dir
            MockJM.return_value.submit.return_value = "j-retry-2"
            result = server._handle_retry_job({
                "job_id": "j-rev",
                "revised_spec": "revised spec with fix",
            })
        assert result["description"] == "revised spec with fix"

    def test_mode_id_added_to_retry_task_args(self, protocol, project_dir, tmp_path):
        """If server has mode_id, it's included in the retry task args."""
        srv = ProtocolMCPServer(protocol, project_dir, mode_id="full")
        job_dir = tmp_path / "j-mode"
        job_dir.mkdir()
        (job_dir / "task.json").write_text(
            json.dumps({"task_id": "t1", "description": "spec"})
        )
        captured = {}
        with patch("snodo.jobs.JobManager") as MockJM:
            MockJM.return_value._job_dir.return_value = job_dir
            MockJM.return_value.submit.side_effect = lambda a: (captured.update(a) or "j-mode-retry")
            srv._handle_retry_job({"job_id": "j-mode"})
        assert captured.get("mode") == "full"
