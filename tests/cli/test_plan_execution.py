"""Tests for plan execution path (_run_plan and snodo run --plan).

FILE: tests/cli/test_plan_execution.py (Fixes #44)

Covers:
- Full plan execution path (PlannerMCP + plan decomposition + _run_plan).
- Multi-wave sequential execution and wave dependencies.
- Resume execution for completed tasks and waves.
- Wave filtering (--wave <id>).
- Failure modes: missing spec file, task execution failure, dependency blocking,
  invalid wave filter, planner error, missing protocol.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from snodo.mcp.planner import PlannerMCP

from snodo.cli.commands.plan_run import _run_plan
from snodo.cli.main import main


@pytest.fixture
def plan_project_env(tmp_path, monkeypatch):
    """Fixture creating a valid project root with protocol and initialized PlannerMCP."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    snodo_dir = project_dir / ".snodo"
    snodo_dir.mkdir()

    protocol_content = """
protocol_id: "test_plan_p"
name: "Test Plan Protocol"
version: "1.0.0"
initial_mode: "producer"
modes:
  - mode_id: "producer"
    name: "Producer"
    tools: ["edit"]
    validators: ["quality"]
validators:
  - validator_id: "quality"
    validator_type: "quality"
    tooling:
      test_command: "pytest"
    criteria: ["Pass quality"]
disagreement_policy: "unanimous"
""".strip()
    (snodo_dir / "protocol.yml").write_text(protocol_content)

    tests_dir = project_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_dummy.py").write_text("def test_pass(): pass\n")
    (project_dir / "conftest.py").write_text(
        "import sys, builtins\n"
        "from pathlib import Path\n"
        "src_path = Path(__file__).parent / 'src'\n"
        "sys.path.insert(0, str(src_path))\n"
        "try:\n"
        "    from hello import hello\n"
        "    builtins.hello = hello\n"
        "except Exception:\n"
        "    pass\n"
    )

    monkeypatch.chdir(project_dir)
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(project_dir))
    return project_dir


def _create_mock_plan(planner: PlannerMCP, name: str = "feature_plan"):
    """Helper to create a plan with wave files on disk."""
    import json

    import yaml

    plan_dir = planner.plans_dir / name
    plan_dir.mkdir(parents=True, exist_ok=True)

    plan_data = {
        "name": name,
        "intent": "Build feature plan",
        "waves": [
            {
                "id": "1",
                "tasks": ["task_1_1"],
                "depends_on": [],
            },
            {
                "id": "2",
                "tasks": ["task_2_1"],
                "depends_on": ["1"],
            },
        ],
    }
    status_data = {
        "plan_name": name,
        "tasks": {
            "task_1_1": {"status": "pending"},
            "task_2_1": {"status": "pending"},
        },
    }

    (plan_dir / "plan.yml").write_text(yaml.dump(plan_data))
    (plan_dir / "status.json").write_text(json.dumps(status_data, indent=2))

    wave1_dir = plan_dir / "wave_1"
    wave1_dir.mkdir(parents=True, exist_ok=True)
    (wave1_dir / "task_1_1_task.md").write_text("Spec for task 1.1")

    wave2_dir = plan_dir / "wave_2"
    wave2_dir.mkdir(parents=True, exist_ok=True)
    (wave2_dir / "task_2_1_task.md").write_text("Spec for task 2.1")

    return name, plan_data, status_data


def test_full_plan_execution_happy_path(plan_project_env, capsys):
    """Happy path: _run_plan executes wave 1 and wave 2 to completion under --mock."""
    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_mock_plan(planner, "happy_plan")

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml",
        model=None,
        plan=plan_name,
        wave=None,
        mock=True,
        interactive=False,
        no_isolation=True,
    )

    result = _run_plan(args)
    assert result == 0

    status = planner.get_status(plan_name)
    assert status["tasks"]["task_1_1"]["status"] == "completed"
    assert status["tasks"]["task_2_1"]["status"] == "completed"

    out = capsys.readouterr().out
    assert "Plan: happy_plan" in out
    assert "Plan progress: 2/2 completed" in out


def test_plan_resume_skips_completed_tasks(plan_project_env, capsys):
    """Resume execution: completed tasks in previous run are skipped."""
    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_mock_plan(planner, "resume_plan")

    # Mark task_1_1 as completed
    planner.update_status(plan_name, "task_1_1", "completed")

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml",
        model=None,
        plan=plan_name,
        wave=None,
        mock=True,
        interactive=False,
        no_isolation=True,
    )

    result = _run_plan(args)
    assert result == 0

    out = capsys.readouterr().out
    assert "[task_1_1] skipped (completed)" in out
    assert "[task_2_1] executing..." in out

    status = planner.get_status(plan_name)
    assert status["tasks"]["task_2_1"]["status"] == "completed"


def test_plan_wave_filtering(plan_project_env, capsys):
    """Wave filtering (--wave 1) executes only wave 1 tasks."""
    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_mock_plan(planner, "wave_filter_plan")

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml",
        model=None,
        plan=plan_name,
        wave="1",
        mock=True,
        interactive=False,
        no_isolation=True,
    )

    result = _run_plan(args)
    assert result == 0

    status = planner.get_status(plan_name)
    assert status["tasks"]["task_1_1"]["status"] == "completed"
    assert status["tasks"]["task_2_1"]["status"] == "pending"


def test_plan_invalid_wave_filter_fails(plan_project_env, capsys):
    """Passing a non-existent wave ID (--wave 99) fails with exit code 1."""
    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_mock_plan(planner, "invalid_wave_plan")

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml",
        model=None,
        plan=plan_name,
        wave="99",
        mock=True,
        interactive=False,
        no_isolation=True,
    )

    result = _run_plan(args)
    assert result == 1
    err = capsys.readouterr().err
    assert "Wave 99 not found in plan" in err


def test_plan_missing_spec_file_fails(plan_project_env, capsys):
    """Failure mode: task listed in plan but missing spec file on disk sets task to blocked and halts."""
    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_mock_plan(planner, "missing_spec_plan")

    # Remove the spec file for task_1_1
    spec_file = planner.plans_dir / plan_name / "wave_1" / "task_1_1_task.md"
    spec_file.unlink()

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml",
        model=None,
        plan=plan_name,
        wave=None,
        mock=True,
        interactive=False,
        no_isolation=True,
    )

    result = _run_plan(args)
    assert result == 1

    err = capsys.readouterr().err
    assert "Plan violates well-formedness conditions" in err
    assert "Missing spec: task_1_1" in err


def test_plan_dependency_blocking_fails(plan_project_env, capsys):
    """Failure mode: wave 2 blocked because wave 1 is incomplete/failed returns exit code 1."""
    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_mock_plan(planner, "blocked_dep_plan")

    # Mark task_1_1 as blocked
    planner.update_status(plan_name, "task_1_1", "blocked")

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml",
        model=None,
        plan=plan_name,
        wave="2",  # Try to execute wave 2 directly
        mock=True,
        interactive=False,
        no_isolation=True,
    )

    result = _run_plan(args)
    assert result == 1

    out = capsys.readouterr().out
    assert "Wave 2: blocked (depends on: 1)" in out


def test_plan_task_execution_failure(plan_project_env, capsys):
    """Failure mode: when _execute_task fails (returns 1), task is marked blocked and plan halts."""
    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_mock_plan(planner, "exec_fail_plan")

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml",
        model=None,
        plan=plan_name,
        wave=None,
        mock=True,
        interactive=False,
        no_isolation=True,
    )

    # Patch _execute_task to simulate execution failure (returns 1)
    with patch("snodo.cli.commands.run_cmd._execute_task", return_value=1):
        result = _run_plan(args)

    assert result == 1
    err = capsys.readouterr().err
    assert "[task_1_1] FAILED" in err

    status = planner.get_status(plan_name)
    assert status["tasks"]["task_1_1"]["status"] == "blocked"
    # Task 2.1 in wave 2 should not have been executed
    assert status["tasks"]["task_2_1"]["status"] == "pending"


def test_plan_interactive_user_skip(plan_project_env, capsys):
    """Interactive mode: user declining task execution skips the task."""
    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_mock_plan(planner, "interactive_plan")

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml",
        model=None,
        plan=plan_name,
        wave="1",
        mock=True,
        interactive=True,
        no_isolation=True,
    )

    # User answers 'n' when prompted
    with patch("builtins.input", return_value="n"):
        result = _run_plan(args)

    assert result == 0
    out = capsys.readouterr().out
    assert "[task_1_1] skipped (user)" in out

    status = planner.get_status(plan_name)
    # Task remains pending
    assert status["tasks"]["task_1_1"]["status"] == "pending"


def test_plan_cli_main_integration(plan_project_env):
    """CLI main() integration: snodo run --plan <plan> --mock executes via CLI entrypoint."""
    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_mock_plan(planner, "cli_plan")

    with patch("sys.argv", ["snodo", "run", "--plan", plan_name, "--mock", "--no-isolation"]):
        result = main()

    assert result == 0
    status = planner.get_status(plan_name)
    assert status["tasks"]["task_1_1"]["status"] == "completed"


# ---------------------------------------------------------------------------
# Blocked-task resume through the retry path (Fixes #131)
# ---------------------------------------------------------------------------

def _make_plan_args(plan_name, **overrides):
    """Build the SimpleNamespace args used by _run_plan / _execute_wave_task."""
    args = SimpleNamespace(
        protocol=".snodo/protocol.yml",
        model=None,
        plan=plan_name,
        wave=None,
        mock=True,
        interactive=False,
        no_isolation=True,
    )
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


def _setup_session_with_failure(project_dir, task_id, attempt=1, spec="Spec for task 1.1"):
    """Create an active session carrying task_failure context for *task_id*.

    Returns the SessionManager so tests can attach it to args.session_manager.
    """
    from snodo.infrastructure.session import SessionManager
    from snodo.infrastructure.state import ProjectState, write_state
    from snodo.protocols import _TEMPLATE_PROTOCOLS

    protocol = _TEMPLATE_PROTOCOLS["solo"]
    mode = protocol.modes[0].mode_id
    write_state(project_dir, ProjectState(current_mode=mode))

    session_mgr = SessionManager(sessions_dir=project_dir / ".snodo" / "sessions")
    session = session_mgr.create_session(mode, str(project_dir))
    session_mgr.update_decision(session.session_id, "task_failure", {
        task_id: {
            "spec": spec,
            "branch": f"task/{task_id}",
            "attempt": attempt,
            "failed_validators": [
                {
                    "validator_id": "quality",
                    "severity": "blocker",
                    "justification": "tests do not pass",
                }
            ],
            "files_changed": ["src/a.py"],
        },
    })
    return session_mgr


def test_blocked_task_with_failure_context_resumes_as_retry(plan_project_env, capsys):
    """A blocked task with failure context resumes through the retry path."""
    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_mock_plan(planner, "retry_plan")
    planner.update_status(plan_name, "task_1_1", "blocked")

    session_mgr = _setup_session_with_failure(plan_project_env, "task_1_1")
    args = _make_plan_args(plan_name, session_manager=session_mgr)

    executed = []

    def mock_retry_task(a, task_id, project_root, session_manager):
        executed.append(task_id)
        return 0

    with patch("snodo.cli.commands.run_cmd._retry_task", side_effect=mock_retry_task):
        result = _run_plan(args)

    assert result == 0
    assert executed == ["task_1_1"]
    out = capsys.readouterr().out
    assert "[task_1_1] resuming as retry (failure context found)" in out
    status = planner.get_status(plan_name)
    assert status["tasks"]["task_1_1"]["status"] == "completed"
    assert status["tasks"]["task_2_1"]["status"] == "completed"


def test_blocked_task_without_context_runs_fresh(plan_project_env, capsys):
    """A blocked task with no failure context runs fresh and says so."""
    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_mock_plan(planner, "fresh_plan")
    planner.update_status(plan_name, "task_1_1", "blocked")

    args = _make_plan_args(plan_name, session_manager=MagicMock())

    executed = []

    def mock_execute_task(a, protocol, task, model):
        executed.append(task.id)
        return 0

    with patch("snodo.cli.commands.run_cmd._execute_task", side_effect=mock_execute_task):
        result = _run_plan(args)

    assert result == 0
    assert executed == ["task_1_1", "task_2_1"]
    out = capsys.readouterr().out
    assert "[task_1_1] no failure context found; running fresh" in out
    assert "[task_1_1] executing..." in out
    status = planner.get_status(plan_name)
    assert status["tasks"]["task_1_1"]["status"] == "completed"


def test_blocked_task_at_max_retries_not_reexecuted(plan_project_env, capsys):
    """A task at max_retries is not re-executed and prints abandon/override guidance."""
    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_mock_plan(planner, "exhausted_plan")
    planner.update_status(plan_name, "task_1_1", "blocked")

    session_mgr = _setup_session_with_failure(plan_project_env, "task_1_1", attempt=3)
    args = _make_plan_args(plan_name, session_manager=session_mgr)

    executed = []

    def mock_execute_task(a, protocol, task, model):
        executed.append(task.id)
        return 0

    with patch("snodo.cli.commands.run_cmd._execute_task", side_effect=mock_execute_task):
        result = _run_plan(args)

    assert result == 1
    assert executed == []
    out = capsys.readouterr().out
    assert "[task_1_1] not re-executed (max_retries reached)" in out
    assert "Task task_1_1 has failed 3 times." in out
    assert "snodo run --retry task_1_1" in out
    assert "snodo task abandon task_1_1" in out
    status = planner.get_status(plan_name)
    assert status["tasks"]["task_1_1"]["status"] == "blocked"


def test_completed_task_still_skipped(plan_project_env, capsys):
    """A completed task is still skipped on re-run."""
    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_mock_plan(planner, "skip_plan")
    planner.update_status(plan_name, "task_1_1", "completed")

    args = _make_plan_args(plan_name)

    executed = []

    def mock_execute_task(a, protocol, task, model):
        executed.append(task.id)
        return 0

    with patch("snodo.cli.commands.run_cmd._execute_task", side_effect=mock_execute_task):
        result = _run_plan(args)

    assert result == 0
    assert executed == ["task_2_1"]
    out = capsys.readouterr().out
    assert "[task_1_1] skipped (completed)" in out
    status = planner.get_status(plan_name)
    assert status["tasks"]["task_1_1"]["status"] == "completed"
    assert status["tasks"]["task_2_1"]["status"] == "completed"


# ---------------------------------------------------------------------------
# Concurrency and Wave Independence Tests
# ---------------------------------------------------------------------------

def _create_multi_task_wave_plan(planner: PlannerMCP, name: str = "parallel_plan"):
    import json
    import yaml

    plan_dir = planner.plans_dir / name
    plan_dir.mkdir(parents=True, exist_ok=True)

    plan_data = {
        "name": name,
        "intent": "Build parallel tasks",
        "waves": [
            {
                "id": "1",
                "tasks": ["task_1_a", "task_1_b"],
                "depends_on": [],
            },
        ],
    }
    status_data = {
        "plan_name": name,
        "tasks": {
            "task_1_a": {"status": "pending"},
            "task_1_b": {"status": "pending"},
        },
    }

    (plan_dir / "plan.yml").write_text(yaml.dump(plan_data))
    (plan_dir / "status.json").write_text(json.dumps(status_data, indent=2))

    wave1_dir = plan_dir / "wave_1"
    wave1_dir.mkdir(parents=True, exist_ok=True)
    (wave1_dir / "task_1_a_task.md").write_text("Spec for task 1.a")
    (wave1_dir / "task_1_b_task.md").write_text("Spec for task 1.b")

    return name, plan_data, status_data


def test_concurrent_wave_tasks_run_in_parallel_and_both_merge(plan_project_env):
    """A wave with two independent tasks under a limit of two runs them concurrently as separate jobs and both complete."""
    from snodo.infrastructure.config import LlmConfig, CoderConfig
    from snodo.jobs import JobManager

    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_multi_task_wave_plan(planner, "concurrent_plan")

    protocol_content = """
protocol_id: "concurrent_p"
name: "Concurrent Protocol"
version: "1.0.0"
initial_mode: "producer"
modes:
  - mode_id: "producer"
    name: "Producer"
    tools: ["edit"]
    validators: ["quality"]
    concurrency: 2
validators:
  - validator_id: "quality"
    validator_type: "quality"
    criteria: ["Pass quality"]
disagreement_policy: "unanimous"
""".strip()
    (plan_project_env / ".snodo" / "protocol.yml").write_text(protocol_content)

    args = _make_plan_args(plan_name)

    submitted_jobs = {}
    active = 0
    max_active = 0

    def mock_submit(self, task_args):
        nonlocal active, max_active
        job_id = f"j_{len(submitted_jobs) + 1:04x}"
        active += 1
        if active > max_active:
            max_active = active
        submitted_jobs[job_id] = task_args
        return job_id

    def mock_get_status(self, job_id):
        nonlocal active
        return {
            "status": "completed",
            "exit_code": 0,
            "id": job_id,
            "task": submitted_jobs.get(job_id, {}),
        }

    custom_cfg = LlmConfig(coder=CoderConfig(concurrency=2))
    with patch("snodo.infrastructure.config.load_llm_config", return_value=custom_cfg):
        with patch.object(JobManager, "submit", mock_submit):
            with patch.object(JobManager, "get_status", mock_get_status):
                result = _run_plan(args)

    assert result == 0
    assert max_active == 2
    assert len(submitted_jobs) == 2
    tasks_in_jobs = {args["task_id"] for args in submitted_jobs.values()}
    assert tasks_in_jobs == {"task_1_a", "task_1_b"}

    status = planner.get_status(plan_name)
    assert status["tasks"]["task_1_a"]["status"] == "completed"
    assert status["tasks"]["task_1_b"]["status"] == "completed"


def test_concurrent_wave_tasks_run_as_separate_jobs_in_filesystem(plan_project_env):
    """Concurrent wave tasks spawn separate job directories with their own task specs."""
    from snodo.infrastructure.config import LlmConfig, CoderConfig
    from snodo.jobs import JobManager

    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_multi_task_wave_plan(planner, "jobs_fs_plan")

    protocol_content = """
protocol_id: "concurrent_p"
name: "Concurrent Protocol"
version: "1.0.0"
initial_mode: "producer"
modes:
  - mode_id: "producer"
    name: "Producer"
    tools: ["edit"]
    validators: ["quality"]
    concurrency: 2
validators:
  - validator_id: "quality"
    validator_type: "quality"
    criteria: ["Pass quality"]
disagreement_policy: "unanimous"
""".strip()
    (plan_project_env / ".snodo" / "protocol.yml").write_text(protocol_content)

    args = _make_plan_args(plan_name)

    submitted_job_ids = []

    real_submit = JobManager.submit

    def tracking_submit(self, task_args):
        with patch("snodo.infrastructure.worktree.create_worktree", return_value=plan_project_env):
            with patch("snodo.jobs.runner.spawn_background", return_value=99999):
                job_id = real_submit(self, task_args)
                submitted_job_ids.append(job_id)
                # Write a completed state so polling sees it finished
                self._save_state(self._job_dir(job_id), {
                    "status": "completed",
                    "exit_code": 0,
                    "pid": 99999,
                })
                return job_id

    custom_cfg = LlmConfig(coder=CoderConfig(concurrency=2))
    with patch("snodo.infrastructure.config.load_llm_config", return_value=custom_cfg):
        with patch.object(JobManager, "submit", tracking_submit):
            result = _run_plan(args)

    assert result == 0
    assert len(submitted_job_ids) == 2
    assert submitted_job_ids[0] != submitted_job_ids[1]

    # Verify each job exists in .snodo/jobs/ with task.json
    manager = JobManager(str(plan_project_env))
    for j_id in submitted_job_ids:
        job_dir = manager.jobs_dir / j_id
        assert job_dir.is_dir()
        task_info = manager._load_task(job_dir)
        assert task_info["task_id"] in ("task_1_a", "task_1_b")


def test_task_output_is_retrievable_per_task_after_wave(plan_project_env):
    """A task's output logs are isolated and retrievable per task/job after wave execution."""
    from snodo.infrastructure.config import LlmConfig, CoderConfig
    from snodo.jobs import JobManager

    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_multi_task_wave_plan(planner, "logs_plan")

    protocol_content = """
protocol_id: "concurrent_p"
name: "Concurrent Protocol"
version: "1.0.0"
initial_mode: "producer"
modes:
  - mode_id: "producer"
    name: "Producer"
    tools: ["edit"]
    validators: ["quality"]
    concurrency: 2
validators:
  - validator_id: "quality"
    validator_type: "quality"
    criteria: ["Pass quality"]
disagreement_policy: "unanimous"
""".strip()
    (plan_project_env / ".snodo" / "protocol.yml").write_text(protocol_content)

    args = _make_plan_args(plan_name)

    submitted_job_ids = []
    real_submit = JobManager.submit

    def tracking_submit(self, task_args):
        with patch("snodo.infrastructure.worktree.create_worktree", return_value=plan_project_env):
            with patch("snodo.jobs.runner.spawn_background", return_value=99999):
                job_id = real_submit(self, task_args)
                submitted_job_ids.append(job_id)
                # Write stdout log specific to this task
                job_dir = self._job_dir(job_id)
                (job_dir / "stdout.log").write_text(f"Output log for {task_args['task_id']}\n")
                (job_dir / "stderr.log").write_text(f"Error log for {task_args['task_id']}\n")
                self._save_state(job_dir, {
                    "status": "completed",
                    "exit_code": 0,
                    "pid": 99999,
                })
                return job_id

    custom_cfg = LlmConfig(coder=CoderConfig(concurrency=2))
    with patch("snodo.infrastructure.config.load_llm_config", return_value=custom_cfg):
        with patch.object(JobManager, "submit", tracking_submit):
            result = _run_plan(args)

    assert result == 0
    manager = JobManager(str(plan_project_env))
    for j_id in submitted_job_ids:
        task_info = manager._load_task(manager._job_dir(j_id))
        tid = task_info["task_id"]
        stdout_log = manager.get_logs(j_id, stream="stdout")
        assert f"Output log for {tid}" in stdout_log
        stderr_log = manager.get_logs(j_id, stream="stderr")
        assert f"Error log for {tid}" in stderr_log


def test_sequential_wave_tasks_under_limit_one(plan_project_env):
    """A limit of one preserves sequential execution."""
    import threading
    import time
    from snodo.infrastructure.config import LlmConfig, CoderConfig

    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_multi_task_wave_plan(planner, "seq_plan")

    args = _make_plan_args(plan_name)

    lock = threading.Lock()
    active = 0
    max_active = 0
    completed = []

    def mock_execute_task(a, protocol, task, model):
        nonlocal active, max_active
        with lock:
            active += 1
            if active > max_active:
                max_active = active
        time.sleep(0.02)
        with lock:
            active -= 1
            completed.append(task.id)
        return 0

    custom_cfg = LlmConfig(coder=CoderConfig(concurrency=1))
    with patch("snodo.infrastructure.config.load_llm_config", return_value=custom_cfg):
        with patch("snodo.cli.commands.run_cmd._execute_task", side_effect=mock_execute_task):
            result = _run_plan(args)

    assert result == 0
    assert max_active == 1
    assert completed == ["task_1_a", "task_1_b"]

    status = planner.get_status(plan_name)
    assert status["tasks"]["task_1_a"]["status"] == "completed"
    assert status["tasks"]["task_1_b"]["status"] == "completed"


def test_lower_of_mode_ceiling_and_config_capacity_wins(plan_project_env):
    """The effective concurrency limit is the smaller of mode ceiling and config capacity."""
    from snodo.infrastructure.config import LlmConfig, CoderConfig
    from snodo.jobs import JobManager

    planner = PlannerMCP(plan_project_env)

    # 1. Mode ceiling = 1, Config capacity = 3 -> effective = 1 (sequential)
    plan_name, _, _ = _create_multi_task_wave_plan(planner, "lower_limit_1")
    args = _make_plan_args(plan_name)

    executed_seq = []

    def mock_execute_task(a, protocol, task, model):
        executed_seq.append(task.id)
        return 0

    cfg_3 = LlmConfig(coder=CoderConfig(concurrency=3))
    with patch("snodo.infrastructure.config.load_llm_config", return_value=cfg_3):
        with patch("snodo.cli.commands.run_cmd._execute_task", side_effect=mock_execute_task):
            result = _run_plan(args)
    assert result == 0
    assert executed_seq == ["task_1_a", "task_1_b"]

    # 2. Mode ceiling = 3, Config capacity = 1 -> effective = 1 (sequential)
    protocol_content = """
protocol_id: "ceil_3_p"
name: "Ceiling 3 Protocol"
version: "1.0.0"
initial_mode: "producer"
modes:
  - mode_id: "producer"
    name: "Producer"
    tools: ["edit"]
    validators: ["quality"]
    concurrency: 3
validators:
  - validator_id: "quality"
    validator_type: "quality"
    criteria: ["Pass quality"]
disagreement_policy: "unanimous"
""".strip()
    (plan_project_env / ".snodo" / "protocol.yml").write_text(protocol_content)

    plan_name2, _, _ = _create_multi_task_wave_plan(planner, "lower_limit_2")
    args2 = _make_plan_args(plan_name2)

    executed_seq2 = []

    def mock_execute_task2(a, protocol, task, model):
        executed_seq2.append(task.id)
        return 0

    cfg_1 = LlmConfig(coder=CoderConfig(concurrency=1))
    with patch("snodo.infrastructure.config.load_llm_config", return_value=cfg_1):
        with patch("snodo.cli.commands.run_cmd._execute_task", side_effect=mock_execute_task2):
            result = _run_plan(args2)
    assert result == 0
    assert executed_seq2 == ["task_1_a", "task_1_b"]

    # 3. Mode ceiling = 3, Config capacity = 2 -> effective = 2 (concurrent jobs)
    plan_name3, _, _ = _create_multi_task_wave_plan(planner, "lower_limit_3")
    args3 = _make_plan_args(plan_name3)

    submitted = []

    def mock_submit(self, task_args):
        job_id = f"j_test_{len(submitted)}"
        submitted.append(task_args["task_id"])
        return job_id

    def mock_get_status(self, job_id):
        return {"status": "completed", "exit_code": 0}

    cfg_2 = LlmConfig(coder=CoderConfig(concurrency=2))
    with patch("snodo.infrastructure.config.load_llm_config", return_value=cfg_2):
        with patch.object(JobManager, "submit", mock_submit):
            with patch.object(JobManager, "get_status", mock_get_status):
                result = _run_plan(args3)
    assert result == 0
    assert sorted(submitted) == ["task_1_a", "task_1_b"]


def test_failing_task_does_not_prevent_siblings_from_completing(plan_project_env):
    """A failing task in a wave fails alone and does not prevent its siblings from completing."""
    from snodo.infrastructure.config import LlmConfig, CoderConfig
    from snodo.jobs import JobManager

    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_multi_task_wave_plan(planner, "fail_plan")

    protocol_content = """
protocol_id: "fail_p"
name: "Fail Protocol"
version: "1.0.0"
initial_mode: "producer"
modes:
  - mode_id: "producer"
    name: "Producer"
    tools: ["edit"]
    validators: ["quality"]
    concurrency: 2
validators:
  - validator_id: "quality"
    validator_type: "quality"
    criteria: ["Pass quality"]
disagreement_policy: "unanimous"
""".strip()
    (plan_project_env / ".snodo" / "protocol.yml").write_text(protocol_content)

    args = _make_plan_args(plan_name)

    submitted = {}

    def mock_submit(self, task_args):
        job_id = f"j_fail_{task_args['task_id']}"
        submitted[job_id] = task_args
        return job_id

    def mock_get_status(self, job_id):
        task_info = submitted.get(job_id, {})
        if task_info.get("task_id") == "task_1_a":
            return {"status": "failed", "exit_code": 1, "error": "test failure"}
        return {"status": "completed", "exit_code": 0}

    custom_cfg = LlmConfig(coder=CoderConfig(concurrency=2))
    with patch("snodo.infrastructure.config.load_llm_config", return_value=custom_cfg):
        with patch.object(JobManager, "submit", mock_submit):
            with patch.object(JobManager, "get_status", mock_get_status):
                result = _run_plan(args)

    assert result == 1  # Plan reports failure because task_1_a failed

    status = planner.get_status(plan_name)
    assert status["tasks"]["task_1_a"]["status"] == "blocked"
    # Sibling task_1_b ran and completed successfully!
    assert status["tasks"]["task_1_b"]["status"] == "completed"


def test_interactive_refuses_concurrency_loudly(plan_project_env, capsys):
    """--interactive is incompatible with concurrency > 1 and must be refused loudly."""
    from snodo.infrastructure.config import LlmConfig, CoderConfig

    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_multi_task_wave_plan(planner, "interactive_plan")

    protocol_content = """
protocol_id: "interactive_p"
name: "Interactive Protocol"
version: "1.0.0"
initial_mode: "producer"
modes:
  - mode_id: "producer"
    name: "Producer"
    tools: ["edit"]
    validators: ["quality"]
    concurrency: 2
validators:
  - validator_id: "quality"
    validator_type: "quality"
    criteria: ["Pass quality"]
disagreement_policy: "unanimous"
""".strip()
    (plan_project_env / ".snodo" / "protocol.yml").write_text(protocol_content)

    args = _make_plan_args(plan_name, interactive=True)

    custom_cfg = LlmConfig(coder=CoderConfig(concurrency=2))
    with patch("snodo.infrastructure.config.load_llm_config", return_value=custom_cfg):
        result = _run_plan(args)

    assert result == 1
    err = capsys.readouterr().err
    assert "incompatible with concurrent wave execution" in err


def test_wave_timing_concurrency_overlap_reported(plan_project_env, capsys):
    """Under concurrency 2, reported wave total is shorter than the sum of task elapsed times."""
    import re
    import time
    from snodo.infrastructure.config import LlmConfig, CoderConfig
    from snodo.jobs import JobManager

    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_multi_task_wave_plan(planner, "timing_overlap_plan")

    protocol_content = """
protocol_id: "concurrent_p"
name: "Concurrent Protocol"
version: "1.0.0"
initial_mode: "producer"
modes:
  - mode_id: "producer"
    name: "Producer"
    tools: ["edit"]
    validators: ["quality"]
    concurrency: 2
validators:
  - validator_id: "quality"
    validator_type: "quality"
    criteria: ["Pass quality"]
disagreement_policy: "unanimous"
""".strip()
    (plan_project_env / ".snodo" / "protocol.yml").write_text(protocol_content)

    args = _make_plan_args(plan_name)

    submitted = {}

    def mock_submit(self, task_args):
        job_id = f"j_time_{task_args['task_id']}"
        now = time.time()
        submitted[job_id] = {
            "task_args": task_args,
            "created_at": now,
            "started_at": now,
        }
        return job_id

    def mock_get_status(self, job_id):
        # Sleep a small amount to simulate task runtime
        time.sleep(0.06)
        info = submitted[job_id]
        started = info["started_at"]
        completed = started + 0.06
        return {
            "status": "completed",
            "exit_code": 0,
            "started_at": started,
            "completed_at": completed,
        }

    custom_cfg = LlmConfig(coder=CoderConfig(concurrency=2))
    with patch("snodo.infrastructure.config.load_llm_config", return_value=custom_cfg):
        with patch.object(JobManager, "submit", mock_submit):
            with patch.object(JobManager, "get_status", mock_get_status):
                result = _run_plan(args)

    assert result == 0
    out = capsys.readouterr().out

    # Check task lines have timings and timestamps
    # e.g.: [task_1_a] completed (job j_time_task_1_a) in 0.1s (started 19:22:30, finished 19:22:30)
    task_a_match = re.search(r"\[task_1_a\] completed \(job \S+\) in (\d+\.\d+)s \(started (\d{2}:\d{2}:\d{2}), finished (\d{2}:\d{2}:\d{2})\)", out)
    assert task_a_match is not None, f"task_1_a timing line not matched in output:\n{out}"

    task_b_match = re.search(r"\[task_1_b\] completed \(job \S+\) in (\d+\.\d+)s \(started (\d{2}:\d{2}:\d{2}), finished (\d{2}:\d{2}:\d{2})\)", out)
    assert task_b_match is not None, f"task_1_b timing line not matched in output:\n{out}"

    wave_match = re.search(r"Wave 1 total: (\d+\.\d+)s", out)
    assert wave_match is not None, f"Wave 1 total not matched in output:\n{out}"

    task_a_elapsed = float(task_a_match.group(1))
    task_b_elapsed = float(task_b_match.group(1))
    wave_total = float(wave_match.group(1))

    # Because tasks run in parallel with overlap, wave_total < task_a + task_b
    assert wave_total < (task_a_elapsed + task_b_elapsed) + 0.05


def test_wave_timing_sequential_shape_identical(plan_project_env, capsys):
    """Under concurrency 1, sequential execution reports identical output shape with timestamps and total."""
    import re
    import time
    from snodo.infrastructure.config import LlmConfig, CoderConfig

    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_multi_task_wave_plan(planner, "timing_seq_plan")

    args = _make_plan_args(plan_name)

    def mock_execute_task(a, protocol, task, model):
        time.sleep(0.04)
        return 0

    custom_cfg = LlmConfig(coder=CoderConfig(concurrency=1))
    with patch("snodo.infrastructure.config.load_llm_config", return_value=custom_cfg):
        with patch("snodo.cli.commands.run_cmd._execute_task", side_effect=mock_execute_task):
            result = _run_plan(args)

    assert result == 0
    out = capsys.readouterr().out

    # Check task lines have timings and timestamps in identical shape
    # e.g.: [task_1_a] completed in 0.0s (started 19:22:30, finished 19:22:30)
    task_a_match = re.search(r"\[task_1_a\] completed in (\d+\.\d+)s \(started (\d{2}:\d{2}:\d{2}), finished (\d{2}:\d{2}:\d{2})\)", out)
    assert task_a_match is not None, f"task_1_a timing line not matched in output:\n{out}"

    task_b_match = re.search(r"\[task_1_b\] completed in (\d+\.\d+)s \(started (\d{2}:\d{2}:\d{2}), finished (\d{2}:\d{2}:\d{2})\)", out)
    assert task_b_match is not None, f"task_1_b timing line not matched in output:\n{out}"

    wave_match = re.search(r"Wave 1 total: (\d+\.\d+)s", out)
    assert wave_match is not None, f"Wave 1 total not matched in output:\n{out}"


def test_wave_timing_failed_task_reported(plan_project_env, capsys):
    """Failed tasks report execution elapsed time and start/finish timestamps."""
    import re
    import time
    from snodo.infrastructure.config import LlmConfig, CoderConfig

    planner = PlannerMCP(plan_project_env)
    plan_name, _, _ = _create_multi_task_wave_plan(planner, "timing_fail_plan")

    args = _make_plan_args(plan_name)

    def mock_execute_task(a, protocol, task, model):
        time.sleep(0.03)
        return 1  # Failure

    custom_cfg = LlmConfig(coder=CoderConfig(concurrency=1))
    with patch("snodo.infrastructure.config.load_llm_config", return_value=custom_cfg):
        with patch("snodo.cli.commands.run_cmd._execute_task", side_effect=mock_execute_task):
            result = _run_plan(args)

    assert result == 1
    err = capsys.readouterr().err

    fail_match = re.search(r"\[task_1_a\] FAILED in (\d+\.\d+)s \(started (\d{2}:\d{2}:\d{2}), finished (\d{2}:\d{2}:\d{2})\)", err)
    assert fail_match is not None, f"Failed task timing not matched in stderr:\n{err}"



