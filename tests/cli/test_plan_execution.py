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
