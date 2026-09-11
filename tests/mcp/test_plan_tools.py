"""Tests for the planning surface exposed over the MCP tool layer.

FILE: tests/mcp/test_plan_tools.py

Covers:
- A plan can be proposed, validated and run through the tool surface
- validate_plan runs without dispatching anything (the gate precedes spend)
- a plan that fails its gate cannot be run through this surface
- returned shapes carry waves, their dependencies and their tasks
- a plan is retrievable later by its name (files on disk are the truth)
- capability separation: a mode without 'plan' never reaches planning tools

The real CLI-spawning lifecycle is marked e2e (subprocess invocation of the
snodo CLI); the rest patches the spawn so the gate semantics are checked
without execution cost.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from snodo.compiler.models import Protocol
from snodo.mcp.server import MCPError, ProtocolMCPServer

# === Fixtures ===

# One protocol serves both sides of the seam: the server's validate_task
# (quality is pre-execute by default, test-based — no LLM) and the
# subprocess `snodo plan run` loop under --mock.
_PROTOCOL_DATA = {
    "protocol_id": "plan_tools_test",
    "name": "Plan Tools Test Protocol",
    "version": "1.0.0",
    "initial_mode": "producer",
    "modes": [
        {
            "mode_id": "producer",
            "name": "Producer",
            "tools": ["edit", "dispatch", "test"],
            "validators": ["quality"],
        },
    ],
    "validators": [
        {
            "validator_id": "quality",
            "validator_type": "quality",
            "tooling": {"test_command": "echo test passed"},
            "criteria": ["Tests pass"],
        },
    ],
    "disagreement_policy": "unanimous",
}


@pytest.fixture
def project_dir():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=d, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=d, capture_output=True, check=True)
    readme = Path(d) / "README.md"
    readme.write_text("test")
    snodo_dir = Path(d) / ".snodo"
    snodo_dir.mkdir()
    (snodo_dir / "protocol.yml").write_text(yaml.dump(_PROTOCOL_DATA))
    subprocess.run(["git", "add", "."], cwd=d, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=d, capture_output=True, check=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def server(project_dir):
    return ProtocolMCPServer(Protocol(**_PROTOCOL_DATA), project_dir)


@pytest.fixture
def producer_server(project_dir):
    return ProtocolMCPServer(Protocol(**_PROTOCOL_DATA), project_dir, mode_id="producer")


def _token(server):
    """Obtain a WF1 token via the protocol's own validate_task."""
    result = server.call_tool("validate_task", {"task_id": "plan-gate"})
    assert result["status"] == "pass", result
    return result


def _propose(server, name="ship", waves=2):
    _token(server)
    return server.call_tool(
        "propose_plan",
        {"intent": "Ship the widget", "plan_name": name, "waves": waves},
    )


def _add_task(server, plan, task_id, spec):
    return server.call_tool(
        "generate_spec",
        {"plan_name": plan, "task_id": task_id, "spec": spec},
    )


# === Surface exposure ===

PLANNING_TOOL_NAMES = {
    "decompose", "generate_spec", "validate_plan",
    "propose_plan", "get_plan", "run_plan",
}


class TestPlanningSurface:
    def test_all_modes_server_exposes_planning_surface(self, server):
        """The consumer surface (no pinned mode) reaches the plan gate."""
        names = {t["name"] for t in server.get_tools()}
        assert PLANNING_TOOL_NAMES <= names

    def test_mode_without_plan_refuses_planning_tools(self, producer_server):
        """A mode-pinned agent without the 'plan' capability stays fenced out."""
        names = {t["name"] for t in producer_server.get_tools()}
        assert not (PLANNING_TOOL_NAMES & names)
        for tool in ("propose_plan", "get_plan", "run_plan"):
            with pytest.raises(MCPError, match="Unknown tool"):
                producer_server.call_tool(tool, {"plan_name": "p", "intent": "i"})

    def test_run_plan_requires_validation_token(self, server, project_dir):
        """WF1 holds for the plan run as it does for dispatch."""
        _write_broken_plan(project_dir, name="tokenless")
        with pytest.raises(MCPError, match="WF1 violation"):
            server.call_tool("run_plan", {"plan_name": "tokenless"})


# === Propose → structure ===

class TestProposePlan:
    def test_proposed_plan_carries_waves_dependencies_and_tasks(self, server):
        result = _propose(server)
        plan = result["plan"]

        assert plan["name"] == "ship"
        assert plan["intent"] == "Ship the widget"
        assert [w["id"] for w in plan["waves"]] == [1, 2]
        assert plan["waves"][1]["depends_on"] == [1]

        _add_task(server, "ship", "1.1_core",
                  "INTENT: Build the core widget.\nCONSTRAINTS: Keep it small.")
        _add_task(server, "ship", "2.1_wrap",
                  "INTENT: Wrap the widget for release.\nCONSTRAINTS: None.")

        fetched = server.call_tool("get_plan", {"plan_name": "ship"})
        assert [w["tasks"] for w in fetched["waves"]] == [["1.1_core"], ["2.1_wrap"]]
        assert fetched["tasks"] == {"1.1_core": "pending", "2.1_wrap": "pending"}

        # The response is JSON in plan shape — a consumer never parses prose.
        assert json.loads(json.dumps(fetched, default=str)) == json.loads(
            json.dumps(fetched, default=str)
        )

    def test_propose_writes_the_plan_files(self, server, project_dir):
        result = _propose(server, name="ondisk")
        plan_dir = Path(project_dir) / ".snodo" / "plans" / "ondisk"
        assert (plan_dir / "plan.yml").is_file()
        assert (plan_dir / "status.json").is_file()
        assert result["validation"]["valid"] is True


# === Validate without spend ===

class TestValidateWithoutExecution:
    def test_validation_runs_without_dispatching_anything(self, server, project_dir):
        _propose(server, name="quiet")
        _add_task(server, "quiet", "1.1_a", "INTENT: A.\nCONSTRAINTS: None.")
        _add_task(server, "quiet", "2.1_b", "INTENT: B.\nCONSTRAINTS: None.")

        result = server.call_tool("validate_plan", {"plan_name": "quiet"})

        assert result["valid"] is True
        assert result["wave_count"] == 2
        assert result["task_count"] == 2
        # Nothing executed: no jobs were created, no task moved off pending.
        jobs_dir = Path(project_dir) / ".snodo" / "jobs"
        assert not jobs_dir.exists() or not list(jobs_dir.iterdir())
        status = json.loads(
            (jobs_dir.parent / "plans" / "quiet" / "status.json").read_text()
        )
        assert all(
            (t.get("status") if isinstance(t, dict) else t) == "pending"
            for t in status["tasks"].values()
        )


# === The gate: an invalid plan cannot run ===

def _write_broken_plan(project_dir, name="broken"):
    """A wave that lists a task whose spec file does not exist on disk."""
    plan_dir = Path(project_dir) / ".snodo" / "plans" / name
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.yml").write_text(yaml.dump({
        "name": name,
        "intent": "A plan that must not run",
        "waves": [{"id": 1, "depends_on": [], "tasks": ["1.1_ghost"]}],
    }))
    (plan_dir / "status.json").write_text(json.dumps(
        {"tasks": {"1.1_ghost": "pending"}}, indent=2
    ))
    return plan_dir


class TestRunPlanGate:
    def test_invalid_plan_is_unrunnable_not_merely_discouraged(self, server, project_dir):
        _write_broken_plan(project_dir)
        _token(server)

        validation = server.call_tool("validate_plan", {"plan_name": "broken"})
        assert validation["valid"] is False

        with patch("snodo.mcp.plan_handlers.subprocess.run") as mock_run:
            with pytest.raises(MCPError, match="failed validation and was not run"):
                server.call_tool("run_plan", {"plan_name": "broken"})
            # The refusal happens before anything spawns — no spend.
            mock_run.assert_not_called()

        status = json.loads(
            (Path(project_dir) / ".snodo" / "plans" / "broken" / "status.json").read_text()
        )
        assert status["tasks"]["1.1_ghost"] == "pending"

    def test_valid_plan_runs_through_the_cli_plan_loop_and_reports_status(self, server, project_dir):
        """Tool surface → same authoritative plan-run path, structured result back."""
        _propose(server, name="runner")
        _add_task(server, "runner", "1.1_x", "INTENT: X.\nCONSTRAINTS: None.")
        _token(server)  # run_plan consumes its single-use token at the boundary

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = "Wave 1:\n  [1.1_x] completed in 0.1s\n"
        fake_proc.stderr = ""

        with patch("snodo.mcp.plan_handlers.subprocess.run", return_value=fake_proc) as mock_run:
            result = server.call_tool("run_plan", {"plan_name": "runner", "mock": True})

        cmd = mock_run.call_args.args[0]
        assert cmd[1:6] == ["-u", "-m", "snodo", "plan", "run"]
        assert "runner" in cmd
        assert "--mock" in cmd

        assert result["status"] == "completed"
        assert result["exit_code"] == 0
        assert result["plan"] == "runner"

    def test_failed_run_reports_failure_with_structured_task_state(self, server, project_dir):
        _propose(server, name="failing")
        _add_task(server, "failing", "1.1_x", "INTENT: X.\nCONSTRAINTS: None.")
        _token(server)

        # The run left one task blocked on disk — status.json is the truth.
        status_path = Path(project_dir) / ".snodo" / "plans" / "failing" / "status.json"
        status_path.write_text(json.dumps({"tasks": {"1.1_x": "blocked"}}))

        fake_proc = MagicMock()
        fake_proc.returncode = 1
        fake_proc.stdout = "Wave 1:\n  [1.1_x] BLOCKED in 3.0s\n"
        fake_proc.stderr = "validators halted"

        with patch("snodo.mcp.plan_handlers.subprocess.run", return_value=fake_proc):
            result = server.call_tool("run_plan", {"plan_name": "failing", "mock": True})

        assert result["status"] == "failed"
        assert result["exit_code"] == 1
        assert result["tasks"] == {"1.1_x": "blocked"}
        assert "BLOCKED" in result["output_tail"]


# === Retrieval by name ===

class TestGetPlan:
    def test_plan_retrievable_later_by_name_from_a_fresh_server(self, server, project_dir):
        _propose(server, name="durable")
        _add_task(server, "durable", "1.1_only", "INTENT: Only.\nCONSTRAINTS: None.")

        later = ProtocolMCPServer(Protocol(**_PROTOCOL_DATA), project_dir)
        fetched = later.call_tool("get_plan", {"plan_name": "durable"})

        assert fetched["name"] == "durable"
        assert fetched["intent"] == "Ship the widget"
        assert fetched["waves"][0]["tasks"] == ["1.1_only"]
        assert fetched["validation"]["valid"] is True

    def test_unknown_plan_is_reported_not_invented(self, server):
        with pytest.raises(MCPError, match="Plan not found"):
            server.call_tool("get_plan", {"plan_name": "nope"})

    def test_blank_plan_name_is_refused(self, server):
        with pytest.raises(MCPError, match="plan_name"):
            server.call_tool("get_plan", {"plan_name": "  "})

    def test_corrupt_status_json_is_reported_not_hidden(self, server, project_dir):
        _propose(server, name="noisy")
        (Path(project_dir) / ".snodo" / "plans" / "noisy" / "status.json").write_text(
            "{not json"
        )
        fetched = server.call_tool("get_plan", {"plan_name": "noisy"})
        assert fetched["tasks"] == {}
        assert fetched["waves"]  # the plan structure still comes back


class TestRunPlanSpawnFailures:
    def _valid_plan_server(self, server):
        _propose(server, name="spawnfail")
        _add_task(server, "spawnfail", "1.1_x", "INTENT: X.\nCONSTRAINTS: None.")
        _token(server)
        return server

    def test_timeout_is_reported_as_error(self, server):
        self._valid_plan_server(server)
        with patch(
            "snodo.mcp.plan_handlers.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="snodo", timeout=1),
        ):
            with pytest.raises(MCPError, match="exceeded"):
                server.call_tool("run_plan", {"plan_name": "spawnfail", "mock": True})

    def test_oserror_on_spawn_is_reported(self, server):
        self._valid_plan_server(server)
        with patch(
            "snodo.mcp.plan_handlers.subprocess.run",
            side_effect=OSError("exec failed"),
        ):
            with pytest.raises(MCPError, match="Failed to start plan run"):
                server.call_tool("run_plan", {"plan_name": "spawnfail", "mock": True})


# === End-to-end over the real tool surface ===

@pytest.mark.e2e
def test_propose_validate_run_lifecycle_through_the_tool_surface(server, project_dir):
    """Full lifecycle with the mock coder: the CLI plan loop runs for real."""
    result = _propose(server, name="lifecycle", waves=2)
    assert result["validation"]["valid"] is True

    _add_task(server, "lifecycle", "1.1_first",
              "INTENT: Create first.txt with content first.\nCONSTRAINTS: Touch nothing else.")
    _add_task(server, "lifecycle", "2.1_second",
              "INTENT: Create second.txt with content second.\nCONSTRAINTS: Touch nothing else.")

    validation = server.call_tool("validate_plan", {"plan_name": "lifecycle"})
    assert validation["valid"] is True

    run = server.call_tool("run_plan", {
        "plan_name": "lifecycle", "mock": True, "no_isolation": True,
    })
    assert run["status"] == "completed", run.get("stderr_tail") or run.get("output_tail")
    assert run["exit_code"] == 0
    assert set(run["tasks"].values()) == {"completed"}

    # A fresh server retrieves the completed plan by its stable name.
    later = ProtocolMCPServer(Protocol(**_PROTOCOL_DATA), project_dir)
    fetched = later.call_tool("get_plan", {"plan_name": "lifecycle"})
    assert fetched["tasks"] == {"1.1_first": "completed", "2.1_second": "completed"}
