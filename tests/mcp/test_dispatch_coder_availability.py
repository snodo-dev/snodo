"""Dispatch establishes coder availability in the dispatching process.

FILE: tests/mcp/test_dispatch_coder_availability.py

Readiness checks the coder's PATH in the operator's shell; the job a dispatch
spawns inherits the MCP SERVER's environment, and the two differ. A missing
coder binary used to be discovered at execute — after consensus validation
had been paid for — and recorded as a verdict about the task. Dispatch must
instead refuse, in this process, before the task is submitted: nothing about
the task is wrong, so the token stays live and the task is never marked
blocked on its content.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from snodo.compiler.models import Protocol
from snodo.mcp.server import MCPError, ProtocolMCPServer

from tests.mcp._validate_helpers import validation_passing

AVAIL_PROTOCOL_DATA = {
    "protocol_id": "avail",
    "name": "Availability",
    "version": "1.0.0",
    "modes": [
        {
            "mode_id": "producer",
            "name": "Producer",
            "tools": ["edit", "dispatch", "test"],
            "validators": ["security"],
            "coder": "opencode-cli",
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
def project_dir():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=d, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=d, capture_output=True, check=True)
    (Path(d) / "README.md").write_text("test")
    subprocess.run(["git", "add", "."], cwd=d, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=d, capture_output=True, check=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def server(project_dir):
    protocol = Protocol(**AVAIL_PROTOCOL_DATA)
    return ProtocolMCPServer(protocol, project_dir, mode_id="producer")


def _validated(server):
    with validation_passing(server):
        result = server.call_tool("validate_task", {"task_id": "t_avail"})
    assert result["status"] == "pass"


def test_dispatch_refuses_before_submit_when_coder_binary_absent(server):
    """The configured coder's binary is missing HERE: refuse, submit nothing."""
    _validated(server)
    with patch("snodo.coders.availability.shutil.which", return_value=None), \
            patch("snodo.jobs.JobManager") as mock_jm_cls:
        with pytest.raises(MCPError) as exc_info:
            server.call_tool("dispatch_task", {"task_spec": "implement feature"})

    msg = str(exc_info.value)
    assert "dispatch_task refused" in msg
    assert "opencode" in msg
    # The operator-facing message carries the install command, not a fix
    # hint about the spec.
    assert "Install opencode: curl -fsSL https://opencode.ai/install | bash" in msg
    mock_jm_cls.return_value.submit.assert_not_called()


def test_refused_dispatch_consumes_nothing_and_succeeds_after_install(server):
    """Refusal is not a verdict: the token survives, so dispatching again after
    the coder becomes invocable submits exactly one job, no re-validation."""
    _validated(server)
    with patch("snodo.coders.availability.shutil.which", return_value=None):
        with pytest.raises(MCPError, match="dispatch_task refused"):
            server.call_tool("dispatch_task", {"task_spec": "implement feature"})

    with patch("snodo.coders.availability.shutil.which", return_value="/usr/local/bin/opencode"), \
            patch("snodo.jobs.JobManager") as mock_jm_cls:
        mock_jm = MagicMock()
        mock_jm.submit.return_value = "j_avail1"
        mock_jm_cls.return_value = mock_jm
        result = server.call_tool("dispatch_task", {"task_spec": "implement feature"})

    assert result["status"] == "accepted"
    assert result["task_id"] == "j_avail1"


def test_dispatch_with_present_coder_proceeds_exactly_as_before(server):
    """A coder that IS invocable is dispatched with today's shape and args."""
    _validated(server)
    with patch("snodo.coders.availability.shutil.which", return_value="/usr/local/bin/opencode"), \
            patch("snodo.jobs.JobManager") as mock_jm_cls:
        mock_jm = MagicMock()
        mock_jm.submit.return_value = "j_ok123"
        mock_jm_cls.return_value = mock_jm
        result = server.call_tool(
            "dispatch_task", {"task_spec": "implement feature"}
        )

    assert result["status"] == "accepted"
    assert result["task_id"] == "j_ok123"
    mock_jm.submit.assert_called_once()
    submitted_args = mock_jm.submit.call_args[0][0]
    assert submitted_args["description"] == "implement feature"
    assert submitted_args["cwd"] == server.project_root
    assert submitted_args["mode"] == "producer"


def test_api_coder_without_binaries_never_refused(project_dir):
    """A coder that invokes no program declares no requirements: dispatch is
    unaffected whatever PATH holds."""
    data = {**AVAIL_PROTOCOL_DATA, "protocol_id": "avail_api"}
    data["modes"] = [dict(m) for m in AVAIL_PROTOCOL_DATA["modes"]]
    data["modes"][0].pop("coder")
    server = ProtocolMCPServer(Protocol(**data), project_dir, mode_id="producer")
    _validated(server)
    with patch("snodo.coders.availability.shutil.which", return_value=None), \
            patch("snodo.jobs.JobManager") as mock_jm_cls:
        mock_jm = MagicMock()
        mock_jm.submit.return_value = "j_api1"
        mock_jm_cls.return_value = mock_jm
        result = server.call_tool("dispatch_task", {"task_spec": "implement feature"})
    assert result["status"] == "accepted"
