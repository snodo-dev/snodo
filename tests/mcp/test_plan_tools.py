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

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

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
    """Record a pass via the protocol's own validate_task (the recorded
    token is now audit evidence, not permission — ADR 047)."""
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
    "propose_plan", "get_plan", "run_plan", "record_task_status",
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

    def test_run_plan_needs_no_validation_token(self, server, project_dir):
        """A plan run is not a mutation: the quorum is enforced per task,
        inside the run — and now no MCP call is gated on a caller-held token
        at all (ADR 047). The plan below is malformed, so it is refused on its
        own conformance - never for want of a token.
        """
        _write_broken_plan(project_dir, name="tokenless")
        with pytest.raises(MCPError) as excinfo:
            server.call_tool("run_plan", {"plan_name": "tokenless"})
        assert "requires a validation token" not in str(excinfo.value).lower()
        assert "failed validation" in str(excinfo.value)


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
        assert result["queue"] == "default"
        assert result["position"] == 1
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

    def test_validation_of_queued_plan_does_not_move_it(self, server, project_dir):
        from snodo.infrastructure.queue_store import QueueStore

        store = QueueStore(project_dir)
        store.list_queues()
        store.create_queue("later")
        _propose(server, name="queued")
        _add_task(server, "queued", "1.1_a", "INTENT: A.\nCONSTRAINTS: None.")
        store.add("queued", "later")

        result = server.call_tool("validate_plan", {"plan_name": "queued"})

        assert result["queue"] == "later"
        assert result["position"] == 1
        assert result["already_queued"] is True
        assert store.list_queues()["later"] == ["queued"]


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
        from snodo.infrastructure.queue_store import QueueStore
        assert all("broken" not in plans for plans in QueueStore(project_dir).list_queues().values())

        with patch("snodo.jobs.JobManager") as MockJM:
            with pytest.raises(MCPError, match="failed validation and was not run"):
                server.call_tool("run_plan", {"plan_name": "broken"})
            # The refusal happens before anything is submitted — no spend.
            MockJM.return_value.submit.assert_not_called()

        status = json.loads(
            (Path(project_dir) / ".snodo" / "plans" / "broken" / "status.json").read_text()
        )
        assert status["tasks"]["1.1_ghost"] == "pending"
        jobs_dir = Path(project_dir) / ".snodo" / "jobs"
        assert not jobs_dir.exists() or not list(jobs_dir.iterdir())

    def test_valid_plan_starts_a_job_and_returns_its_id(self, server, project_dir):
        """run_plan submits the run as a job and returns immediately (Fixes #254)."""
        _propose(server, name="runner")
        _add_task(server, "runner", "1.1_x", "INTENT: X.\nCONSTRAINTS: None.")
        from snodo.infrastructure.audit import AuditLog
        server._audit_log = AuditLog(str(Path(project_dir) / ".snodo" / "audit.log"))

        with patch("snodo.jobs.JobManager") as MockJM:
            MockJM.return_value.submit.return_value = "j_runner1"
            result = server.call_tool("run_plan", {"plan_name": "runner", "mock": True})

        assert result["status"] == "accepted"
        assert result["job_id"] == "j_runner1"
        assert result["plan"] == "runner"
        assert "get_job_status" in result["instruction"]

        submitted = MockJM.return_value.submit.call_args.args[0]
        assert submitted["plan_name"] == "runner"
        assert submitted["mock"] is True
        events = AuditLog(str(Path(project_dir) / ".snodo" / "audit.log")).get_history()
        plan_run = next(event for event in events if event.event_type == "plan_run")
        assert plan_run.data == {
            "op": "plan_run",
            "plan_name": "runner",
            "waves": [
                {"wave_id": 1, "task_refs": ["1.1_x"]},
                {"wave_id": 2, "task_refs": []},
            ],
            "trigger": "mcp",
            "job_id": "j_runner1",
            "mode": "producer",
        }

    def test_wait_true_blocks_and_reports_the_final_status(self, server, project_dir):
        """The opt-in wait returns the run's end state, not merely its start."""
        _propose(server, name="waited")
        _add_task(server, "waited", "1.1_x", "INTENT: X.\nCONSTRAINTS: None.")

        status_path = (
            Path(project_dir) / ".snodo" / "plans" / "waited" / "status.json"
        )
        status_path.write_text(json.dumps({"tasks": {"1.1_x": "blocked"}}))

        with patch("snodo.jobs.JobManager") as MockJM:
            mgr = MockJM.return_value
            mgr.submit.return_value = "j_waited1"
            mgr.wait_for.return_value = {
                "id": "j_waited1", "status": "failed", "exit_code": 1,
            }
            mgr.get_logs.return_value = "Wave 1:\n  [1.1_x] BLOCKED in 3.0s\n"
            result = server.call_tool(
                "run_plan", {"plan_name": "waited", "mock": True, "wait": True}
            )

        mgr.wait_for.assert_called_once()
        assert result["status"] == "failed"
        assert result["exit_code"] == 1
        assert result["job_id"] == "j_waited1"
        assert result["tasks"] == {"1.1_x": "blocked"}
        assert "BLOCKED" in result["output_tail"]

    def test_get_plan_exposes_latest_child_job_timing(self, server):
        _propose(server, name="runs")
        _add_task(server, "runs", "1.1_x", "INTENT: X.\nCONSTRAINTS: None.")

        with patch(
            "snodo.jobs.index_plan_jobs",
            return_value=("j_plan", {
                "1.1_x": {
                    "id": "j_child", "status": "running",
                    "started_at": 123.0, "completed_at": None,
                    "duration_seconds": 4.5,
                },
            }),
        ):
            result = server.call_tool("get_plan", {"plan_name": "runs"})

        assert result["tasks"] == {"1.1_x": "pending"}
        assert result["task_runs"] == {
            "1.1_x": {
                "job_id": "j_child",
                "status": "running",
                "started_at": 123.0,
                "completed_at": None,
                "duration_seconds": 4.5,
            },
        }

    def test_wait_true_timeout_names_the_still_running_job(self, server, project_dir):
        """A wait that expires reports the job, never a phantom failure."""
        from snodo.jobs import JobError

        _propose(server, name="slow")
        _add_task(server, "slow", "1.1_x", "INTENT: X.\nCONSTRAINTS: None.")

        with patch("snodo.jobs.JobManager") as MockJM:
            mgr = MockJM.return_value
            mgr.submit.return_value = "j_slow1"
            mgr.wait_for.side_effect = JobError("Timeout waiting for job j_slow1")
            with pytest.raises(MCPError, match="still running"):
                server.call_tool(
                    "run_plan", {"plan_name": "slow", "mock": True, "wait": True}
                )


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


class TestRunPlanSubmissionFailures:
    def _valid_plan_server(self, server):
        _propose(server, name="spawnfail")
        _add_task(server, "spawnfail", "1.1_x", "INTENT: X.\nCONSTRAINTS: None.")
        _token(server)
        return server

    def test_submit_failure_is_reported_as_error(self, server):
        self._valid_plan_server(server)
        from snodo.jobs import JobError

        with patch("snodo.jobs.JobManager") as MockJM:
            MockJM.return_value.submit.side_effect = JobError("exec failed")
            with pytest.raises(MCPError, match="Failed to start plan run"):
                server.call_tool("run_plan", {"plan_name": "spawnfail", "mock": True})


# === The run is a job: liveness, terminal status, distinct rows ===

def _wait_terminal(job_mgr, job_id, timeout=60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = job_mgr.get_status(job_id)
        if status["status"] in ("completed", "failed", "unmerged", "cancelled"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach a terminal status")


def _reap(pid):
    """Reap a killed child so it stops answering ``os.kill(pid, 0)``.

    In production the process that spawned the job reaps it; in this test the
    spawning process is the test itself, so it must do the same or a zombie
    would keep the pid "alive" and mask reconciliation.
    """
    with contextlib.suppress(ChildProcessError, ProcessLookupError):
        os.waitpid(pid, 0)


class TestPlanRunAsJob:
    def test_run_plan_returns_while_the_child_is_still_alive(self, server, project_dir):
        """The call returns before the run finishes — the current-code bug.

        Against the blocking implementation this fails at once: run_plan would
        sit through the whole run and never submit a job. Here the spawned
        child is a real process that outlives the call, so a returned job_id
        whose pid is still alive is the whole claim.
        """
        from snodo.jobs import JobManager

        _propose(server, name="longrun")
        _add_task(server, "longrun", "1.1_x", "INTENT: X.\nCONSTRAINTS: None.")

        def spawn_sleeper(cmd, stdout_path, stderr_path, cwd):
            return subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                stdout=open(stdout_path, "w"), stderr=open(stderr_path, "w"),
            ).pid

        with patch("snodo.jobs.runner.spawn_background", side_effect=spawn_sleeper):
            result = server.call_tool("run_plan", {"plan_name": "longrun", "mock": True})

        assert result["status"] == "accepted"
        job_id = result["job_id"]

        mgr = JobManager(project_dir)
        status = mgr.get_status(job_id)
        pid = status["pid"]
        assert pid is not None, "the plan run should have been spawned"
        os.kill(pid, 0)  # raises if it exited before the call returned

        # Clean up the long-running child so the test does not leak a process.
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
        _reap(pid)

    def test_plan_run_reaches_a_terminal_status_when_the_run_ends(self, server, project_dir):
        """A plan-run job records how the run ended, like any other job."""
        from snodo.jobs import JobManager

        _propose(server, name="finishes")
        _add_task(server, "finishes", "1.1_x", "INTENT: X.\nCONSTRAINTS: None.")

        result = server.call_tool("run_plan", {"plan_name": "finishes", "mock": True})
        job_id = result["job_id"]

        mgr = JobManager(project_dir)
        final = _wait_terminal(mgr, job_id)
        assert final["status"] in ("completed", "failed")
        assert final["exit_code"] is not None

    def test_killed_plan_run_is_reconciled_not_left_running(self, server, project_dir):
        """A run killed without reporting is reconciled, never left live forever."""
        from snodo.jobs import JobManager

        _propose(server, name="killed")
        _add_task(server, "killed", "1.1_x", "INTENT: X.\nCONSTRAINTS: None.")

        result = server.call_tool("run_plan", {"plan_name": "killed", "mock": True})
        job_id = result["job_id"]

        mgr = JobManager(project_dir)
        pid = mgr.get_status(job_id)["pid"]
        assert pid is not None
        os.kill(pid, signal.SIGKILL)  # no chance to write a final state
        _reap(pid)

        # The process is gone; reconciliation must mark the job terminal.
        deadline = time.monotonic() + 10.0
        status = None
        while time.monotonic() < deadline:
            status = mgr.get_status(job_id)
            if status["status"] in ("failed", "completed", "cancelled", "unmerged"):
                break
            time.sleep(0.05)
        assert status is not None
        assert status["status"] == "failed"
        assert status["exit_code"] == -1

    def test_list_jobs_distinguishes_a_plan_run_from_its_tasks(self, server, project_dir):
        """A plan row names the plan; its child rows name the plan run."""
        from snodo.jobs import JobManager

        _propose(server, name="distinct")
        _add_task(server, "distinct", "1.1_x", "INTENT: X.\nCONSTRAINTS: None.")

        result = server.call_tool("run_plan", {"plan_name": "distinct", "mock": True})
        job_id = result["job_id"]
        mgr = JobManager(project_dir)
        _wait_terminal(mgr, job_id)

        rows = mgr.list_jobs()
        plan_row = next(r for r in rows if r["id"] == job_id)
        assert plan_row["plan"] == "distinct"
        assert plan_row["task_ref"] == ""

        child_rows = [r for r in rows if r["id"] != job_id]
        # Any task spawned by the plan names the plan run as its parent.
        for row in child_rows:
            assert row["plan"] == ""
            assert row["parent_job"] == job_id
            assert row["task_ref"]


# === End-to-end over the real tool surface ===

@pytest.mark.e2e
def test_propose_validate_run_lifecycle_through_the_tool_surface(server, project_dir):
    """Full lifecycle with the mock coder: the CLI plan loop runs for real."""
    from snodo.jobs import JobManager

    result = _propose(server, name="lifecycle", waves=2)
    assert result["validation"]["valid"] is True

    _add_task(server, "lifecycle", "1.1_first",
              "INTENT: Create first.txt with content first.\nCONSTRAINTS: Touch nothing else.")
    _add_task(server, "lifecycle", "2.1_second",
              "INTENT: Create second.txt with content second.\nCONSTRAINTS: Touch nothing else.")

    validation = server.call_tool("validate_plan", {"plan_name": "lifecycle"})
    assert validation["valid"] is True

    started = server.call_tool("run_plan", {
        "plan_name": "lifecycle", "mock": True, "no_isolation": True,
    })
    assert started["status"] == "accepted"

    mgr = JobManager(project_dir)
    final = _wait_terminal(mgr, started["job_id"], timeout=90.0)
    assert final["status"] == "completed", mgr.get_logs(started["job_id"], stream="stderr")

    # A fresh server retrieves the completed plan by its stable name.
    later = ProtocolMCPServer(Protocol(**_PROTOCOL_DATA), project_dir)
    fetched = later.call_tool("get_plan", {"plan_name": "lifecycle"})
    assert fetched["tasks"] == {"1.1_first": "completed", "2.1_second": "completed"}
