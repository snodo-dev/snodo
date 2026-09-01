"""Tests for the plan CLI authoring/execution subcommands (Fixes #130).

Covers: plan run, plan add-task, plan add-wave, plan delete — each happy path,
each refusal path, and that plan run reaches _run_plan with the same args
`snodo run --plan` produces.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from snodo.cli.commands.plan_cmd import plan_command


@pytest.fixture
def plan_env(tmp_path, monkeypatch):
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

    monkeypatch.chdir(project_dir)
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(project_dir))
    return project_dir


def _create_plan(project_dir, name="p1"):
    """Create a plan via the planner (empty waves)."""
    from snodo.mcp.planner import PlannerMCP
    planner = PlannerMCP(str(project_dir))
    return planner.decompose("Test plan", name)


def _planner(project_dir):
    from snodo.mcp.planner import PlannerMCP
    return PlannerMCP(str(project_dir))


# ============================================================================
# plan run
# ============================================================================

def test_plan_run_args_carries_all_run_command_parameters(plan_env):
    """plan_run produces a RunArgs instance carrying every parameter derived from `snodo run`."""
    import inspect
    import typer
    from snodo.cli.commands.run_cmd import register
    from snodo.cli.commands.plan_cmd import plan_run

    app = typer.Typer()
    register(app)

    run_cmd_info = next(cmd for cmd in app.registered_commands if (cmd.name or cmd.callback.__name__) == "run")
    run_params = set(inspect.signature(run_cmd_info.callback).parameters.keys())

    captured = {}

    def fake_run_plan(args):
        captured["args"] = args
        return 0

    with patch("snodo.cli.commands.plan_run._run_plan", side_effect=fake_run_plan):
        plan_run("p1", wave=1, interactive=False, protocol=".snodo/protocol.yml", model=None)

    produced_args = captured["args"]
    for param_name in run_params:
        assert hasattr(produced_args, param_name), (
            f"RunArgs object produced by plan_run is missing parameter '{param_name}' defined on the run command"
        )


def test_plan_run_end_to_end_with_mock_coder(tmp_path, monkeypatch):
    """snodo plan run executes a plan end-to-end through real _run_plan with --mock without AttributeError."""
    import json
    import subprocess
    from snodo.mcp.planner import PlannerMCP
    from snodo.cli.commands.run_cmd import RunArgs
    from snodo.cli.commands.plan_run import _run_plan

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    subprocess.run(["git", "init"], cwd=str(project_dir), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(project_dir), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(project_dir), capture_output=True, check=True)
    readme = project_dir / "README.md"
    readme.write_text("test")
    subprocess.run(["git", "add", "."], cwd=str(project_dir), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(project_dir), capture_output=True, check=True)

    snodo_dir = project_dir / ".snodo"
    snodo_dir.mkdir()

    protocol_content = """
protocol_id: "test_plan_e2e"
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
      test_command: "echo test passed"
    criteria: ["Pass quality"]
disagreement_policy: "unanimous"
""".strip()
    (snodo_dir / "protocol.yml").write_text(protocol_content)

    monkeypatch.chdir(project_dir)
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(project_dir))

    planner = PlannerMCP(str(project_dir))
    planner.decompose("Build test task", "e2e_plan")

    spec_dir = snodo_dir / "plans" / "e2e_plan" / "wave_1"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_file = spec_dir / "1.1_test_task.md"
    spec_file.write_text("INTENT: Test task intent.\nCONSTRAINTS: Do not break tests.")

    plan_file = snodo_dir / "plans" / "e2e_plan" / "plan.yml"
    plan_data = yaml.safe_load(plan_file.read_text())
    plan_data["waves"] = [{"id": 1, "tasks": ["1.1_test"]}]
    plan_file.write_text(yaml.dump(plan_data))

    status_file = snodo_dir / "plans" / "e2e_plan" / "status.json"
    status_data = {"version": "1.0", "tasks": {"1.1_test": "pending"}}
    status_file.write_text(json.dumps(status_data))

    subprocess.run(["git", "add", "."], cwd=str(project_dir), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "add plan and spec"], cwd=str(project_dir), capture_output=True, check=True)

    args = RunArgs(
        plan="e2e_plan",
        mock=True,
        no_isolation=True,
    )

    res = _run_plan(args)
    assert res == 0


# ============================================================================
# plan add-task
# ============================================================================

def test_plan_add_task_happy_path(plan_env, capsys):
    """add-task reads the spec file and calls planner.generate_spec."""
    _create_plan(plan_env, "p1")
    spec_file = plan_env / "spec.md"
    spec_file.write_text("Build the models module")

    result = plan_command(SimpleNamespace(
        plan_action="add-task", plan="p1", task_id="1.1_models",
        spec_file=str(spec_file), parent=None, replace=False,
    ))

    assert result == 0
    out = capsys.readouterr().out
    assert "Task 1.1_models added to plan p1" in out

    planner = _planner(plan_env)
    plan = planner.get_plan("p1")
    assert "1.1_models" in [t for w in plan.waves for t in w.tasks]
    spec = (plan_env / ".snodo" / "plans" / "p1" / "wave_1" / "1.1_models_task.md").read_text()
    assert spec == "Build the models module"


def test_plan_add_task_rejects_bad_task_id(plan_env, capsys):
    """add-task rejects a task id that is not <wave>.<seq>_<name>."""
    _create_plan(plan_env, "p1")
    spec_file = plan_env / "spec.md"
    spec_file.write_text("spec")

    result = plan_command(SimpleNamespace(
        plan_action="add-task", plan="p1", task_id="models",
        spec_file=str(spec_file), parent=None, replace=False,
    ))

    assert result == 1
    err = capsys.readouterr().err
    assert "invalid task id 'models'" in err
    assert "1.1_models" in err


def test_plan_add_task_missing_spec_file_is_error(plan_env, capsys):
    """add-task with a missing spec file is an error, not an empty spec."""
    _create_plan(plan_env, "p1")

    result = plan_command(SimpleNamespace(
        plan_action="add-task", plan="p1", task_id="1.1_models",
        spec_file=str(plan_env / "nope.md"), parent=None, replace=False,
    ))

    assert result == 1
    err = capsys.readouterr().err
    assert "spec file not found" in err


def test_plan_add_task_duplicate_refused_without_replace(plan_env, capsys):
    """add-task refuses a duplicate task id unless --replace."""
    _create_plan(plan_env, "p1")
    spec_file = plan_env / "spec.md"
    spec_file.write_text("spec")

    args = SimpleNamespace(
        plan_action="add-task", plan="p1", task_id="1.1_models",
        spec_file=str(spec_file), parent=None, replace=False,
    )
    assert plan_command(args) == 0

    result = plan_command(args)
    assert result == 1
    err = capsys.readouterr().err
    assert "already exists" in err


def test_plan_add_task_replace_overwrites(plan_env, capsys):
    """add-task --replace overwrites an existing task spec."""
    _create_plan(plan_env, "p1")
    spec_file = plan_env / "spec.md"
    spec_file.write_text("v1")

    args = SimpleNamespace(
        plan_action="add-task", plan="p1", task_id="1.1_models",
        spec_file=str(spec_file), parent=None, replace=False,
    )
    assert plan_command(args) == 0

    spec_file.write_text("v2")
    args.replace = True
    result = plan_command(args)
    assert result == 0

    spec = (plan_env / ".snodo" / "plans" / "p1" / "wave_1" / "1.1_models_task.md").read_text()
    assert spec == "v2"


def test_plan_add_task_reports_invalid_plan(plan_env, capsys):
    """add-task reports when the plan no longer verifies."""
    _create_plan(plan_env, "p1")
    spec_file = plan_env / "spec.md"
    spec_file.write_text("spec")

    # Adding a task to wave 3 when only wave 1 exists creates a wave-number
    # gap, so the plan no longer verifies — add-task must report it.
    result = plan_command(SimpleNamespace(
        plan_action="add-task", plan="p1", task_id="3.1_models",
        spec_file=str(spec_file), parent=None, replace=False,
    ))

    assert result == 1
    err = capsys.readouterr().err
    assert "plan is now invalid" in err


# ============================================================================
# plan add-wave
# ============================================================================

def test_plan_add_wave_happy_path(plan_env, capsys):
    """add-wave adds a wave to plan.yml.

    `plan create` scaffolds wave 1, so the first wave a caller adds is 2.
    """
    _create_plan(plan_env, "p1")

    result = plan_command(SimpleNamespace(
        plan_action="add-wave", plan="p1", id="2", depends_on=None,
    ))

    assert result == 0
    out = capsys.readouterr().out
    assert "Wave 2 added to plan p1" in out

    plan_file = plan_env / ".snodo" / "plans" / "p1" / "plan.yml"
    data = yaml.safe_load(plan_file.read_text())
    assert [w["id"] for w in data["waves"]] == [1, 2]


def test_plan_add_wave_with_dependency(plan_env, capsys):
    """add-wave --depends-on 1,2 records dependencies."""
    _create_plan(plan_env, "p1")  # scaffolds wave 1
    assert plan_command(SimpleNamespace(
        plan_action="add-wave", plan="p1", id="2", depends_on=None)) == 0

    result = plan_command(SimpleNamespace(
        plan_action="add-wave", plan="p1", id="3", depends_on="1,2",
    ))

    assert result == 0
    plan_file = plan_env / ".snodo" / "plans" / "p1" / "plan.yml"
    data = yaml.safe_load(plan_file.read_text())
    w3 = next(w for w in data["waves"] if w["id"] == 3)
    assert w3["depends_on"] == [1, 2]


def test_plan_add_wave_refuses_non_integer_id(plan_env, capsys):
    """add-wave refuses a non-integer wave id by name."""
    _create_plan(plan_env, "p1")

    result = plan_command(SimpleNamespace(
        plan_action="add-wave", plan="p1", id="abc", depends_on=None,
    ))

    assert result == 1
    err = capsys.readouterr().err
    assert "wave id 'abc' is not an integer" in err


def test_plan_add_wave_existing_is_idempotent(plan_env, capsys):
    """Adding wave 1 after `plan create` (which scaffolds wave 1) is the natural
    next command and must not error — it is an idempotent no-op (Fixes #192)."""
    _create_plan(plan_env, "p1")  # scaffolds wave 1

    result = plan_command(SimpleNamespace(
        plan_action="add-wave", plan="p1", id="1", depends_on=None,
    ))

    assert result == 0
    out = capsys.readouterr().out
    assert "already exists" in out

    plan_file = plan_env / ".snodo" / "plans" / "p1" / "plan.yml"
    data = yaml.safe_load(plan_file.read_text())
    # The wave was not duplicated.
    assert [w["id"] for w in data["waves"]] == [1]


def test_plan_add_wave_existing_updates_dependencies(plan_env, capsys):
    """add-wave on an existing wave with --depends-on updates its dependencies
    rather than erroring."""
    _create_plan(plan_env, "p1")  # scaffolds wave 1
    assert plan_command(SimpleNamespace(
        plan_action="add-wave", plan="p1", id="2", depends_on=None)) == 0

    result = plan_command(SimpleNamespace(
        plan_action="add-wave", plan="p1", id="1", depends_on="2",
    ))
    assert result == 0

    plan_file = plan_env / ".snodo" / "plans" / "p1" / "plan.yml"
    data = yaml.safe_load(plan_file.read_text())
    w1 = next(w for w in data["waves"] if w["id"] == 1)
    assert w1["depends_on"] == [2]


def test_plan_add_wave_refuses_unknown_dependency(plan_env, capsys):
    """add-wave refuses a dependency on a wave that does not exist."""
    _create_plan(plan_env, "p1")

    result = plan_command(SimpleNamespace(
        plan_action="add-wave", plan="p1", id="2", depends_on="9",
    ))

    assert result == 1
    err = capsys.readouterr().err
    assert "depends on wave 9, which does not exist" in err


# ============================================================================
# plan delete
# ============================================================================

def test_plan_delete_happy_path(plan_env, capsys):
    """delete removes the plan directory."""
    _create_plan(plan_env, "p1")

    result = plan_command(SimpleNamespace(plan_action="delete", name="p1", force=False))

    assert result == 0
    out = capsys.readouterr().out
    assert "Plan 'p1' deleted." in out
    assert not (plan_env / ".snodo" / "plans" / "p1").exists()


def test_plan_delete_refuses_active_tasks(plan_env, capsys):
    """delete without --force refuses when a task is completed or in_progress."""
    _create_plan(plan_env, "p1")
    planner = _planner(plan_env)
    spec_file = plan_env / "spec.md"
    spec_file.write_text("spec")
    planner.generate_spec("p1", "1.1_models", "spec")
    planner.update_status("p1", "1.1_models", "completed")

    result = plan_command(SimpleNamespace(plan_action="delete", name="p1", force=False))

    assert result == 1
    err = capsys.readouterr().err
    assert "refusing to delete plan with active tasks" in err
    assert "1.1_models" in err
    assert (plan_env / ".snodo" / "plans" / "p1").exists()


def test_plan_delete_force_overrides_active_tasks(plan_env, capsys):
    """delete --force removes a plan even with active tasks."""
    _create_plan(plan_env, "p1")
    planner = _planner(plan_env)
    spec_file = plan_env / "spec.md"
    spec_file.write_text("spec")
    planner.generate_spec("p1", "1.1_models", "spec")
    planner.update_status("p1", "1.1_models", "in_progress")

    result = plan_command(SimpleNamespace(plan_action="delete", name="p1", force=True))

    assert result == 0
    assert not (plan_env / ".snodo" / "plans" / "p1").exists()


def test_plan_delete_missing_plan(plan_env, capsys):
    """delete of a nonexistent plan is an error."""
    result = plan_command(SimpleNamespace(plan_action="delete", name="nope", force=False))

    assert result == 1
    err = capsys.readouterr().err
    assert "plan not found: nope" in err
