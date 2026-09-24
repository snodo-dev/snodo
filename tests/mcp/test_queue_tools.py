"""MCP queue capabilities mirror the CLI and queue runs use the job surface."""

import subprocess
from types import SimpleNamespace

import pytest

from snodo.compiler.models import Protocol
from snodo.infrastructure.queue_store import QueueStore
from snodo.mcp.server import MCPError, ProtocolMCPServer
from snodo.jobs.runner import build_command


def _protocol(tools):
    return Protocol(**{
        "protocol_id": "queue-test", "name": "Queue test", "version": "1.0.0",
        "modes": [{"mode_id": "orchestrator", "name": "Orchestrator", "tools": tools, "validators": ["security"]}],
        "validators": [{"validator_id": "security", "validator_type": "security", "criteria": ["Check security"]}],
        "disagreement_policy": "unanimous", "initial_mode": "orchestrator",
    })


@pytest.fixture
def project(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / ".snodo").mkdir()
    return tmp_path


def test_queue_tools_are_mode_granted_and_operations_match_store(project):
    server = ProtocolMCPServer(_protocol(["queue"]), str(project), mode_id="orchestrator")
    names = {tool["name"] for tool in server.get_tools()}
    assert {"queue_list", "queue_create", "queue_move", "queue_remove", "queue_validate", "queue_run"} <= names
    assert {"get_job_status", "get_job_logs"} <= names
    assert server.call_tool("queue_create", {"name": "build"}) == {"ok": True, "queue": "build"}
    store = QueueStore(project)
    store.add("plan-a")
    store.add("plan-b")
    assert server.call_tool("queue_move", {"plan": "plan-b", "front": True})["position"] == "front"
    assert [p["name"] for p in server.call_tool("queue_list")["queues"]["default"]] == ["plan-b", "plan-a"]
    assert server.call_tool("queue_validate", {"queue": "default"})["queues"]["default"]["plans"][0]["name"] == "plan-b"
    assert server.call_tool("queue_remove", {"plan": "plan-b"}) == {
        "ok": True, "plan": "plan-b", "queue": "default",
    }
    with pytest.raises(MCPError, match="Plan is not queued: plan-b"):
        server.call_tool("queue_remove", {"plan": "plan-b"})
    assert [p["name"] for p in server.call_tool("queue_list")["queues"]["default"]] == ["plan-a"]
    with pytest.raises(MCPError, match="already exists"):
        server.call_tool("queue_create", {"name": "build"})


def test_queue_tools_are_not_exposed_without_mode_grant(project):
    server = ProtocolMCPServer(_protocol(["read"]), str(project), mode_id="orchestrator")
    assert not ({"queue_list", "queue_create", "queue_move", "queue_remove", "queue_validate", "queue_run"} &
                {tool["name"] for tool in server.get_tools()})
    with pytest.raises(MCPError, match="Unknown tool"):
        server.call_tool("queue_list")


def test_queue_remove_refuses_running_plan(project):
    plan_dir = project / ".snodo" / "plans" / "busy"
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.yml").write_text(
        "intent: test\nwaves:\n  - id: 1\n    tasks:\n      - 1.1_work\n"
    )
    (plan_dir / "status.json").write_text('{"tasks": {"1.1_work": "in_progress"}}')
    QueueStore(project).add("busy")
    server = ProtocolMCPServer(_protocol(["queue"]), str(project), mode_id="orchestrator")
    with pytest.raises(MCPError, match="Cannot remove plan while it is running: busy"):
        server.call_tool("queue_remove", {"plan": "busy"})
    assert QueueStore(project).list_queues() == {"default": ["busy"]}


def test_queue_run_job_command_preserves_cli_arguments():
    command = build_command("/jobs/j_1", {
        "queue_run": True, "queues": "default,build", "non_blocking": True,
        "parallel_run": 3, "protocol": ".snodo/protocol.yml", "mock": True,
    })
    assert command[5:8] == ["queue", "run", "default,build"]
    assert "--non-blocking" in command
    assert command[command.index("--parallel-run") + 1] == "3"
    assert "--mock" in command
    assert command[command.index("--protocol") + 1] == ".snodo/protocol.yml"


def test_queue_run_returns_submitted_job_id_without_waiting(project, monkeypatch):
    server = ProtocolMCPServer(_protocol(["queue"]), str(project), mode_id="orchestrator")
    submitted = {}

    monkeypatch.setattr("snodo.protocols.load_protocol", lambda path: SimpleNamespace(queue=object()))

    class Manager:
        def __init__(self, root):
            assert root == str(project)

        def submit(self, args):
            submitted.update(args)
            return "j_queue123"

    monkeypatch.setattr("snodo.jobs.JobManager", Manager)
    result = server.call_tool("queue_run", {"mock": True})
    assert result["job_id"] == "j_queue123"
    assert result["status"] == "accepted"
    assert submitted["queue_run"] is True
    assert submitted["queues"] is None
    assert submitted["mock"] is True
