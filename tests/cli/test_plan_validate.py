"""Tests for snodo plan validate command and plan run pre-verification.

FILE: tests/cli/test_plan_validate.py
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from snodo.cli.commands.plan_cmd import plan_command
from snodo.cli.commands.plan_run import _run_plan


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

    tests_dir = project_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_dummy.py").write_text("def test_pass(): pass\n")

    monkeypatch.chdir(project_dir)
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(project_dir))
    return project_dir


def _create_hand_authored_plan(project_dir, name="hand_plan", missing_spec=False, wave_gap=False, cycle=False, warning_no_tasks=False):
    """Helper to create a hand-authored plan structure."""
    plans_dir = project_dir / ".snodo" / "plans" / name
    plans_dir.mkdir(parents=True, exist_ok=True)

    if wave_gap:
        waves = [
            {"id": 1, "depends_on": [], "tasks": ["1.1_setup"]},
            {"id": 3, "depends_on": [1], "tasks": ["3.1_deploy"]},
        ]
    elif cycle:
        waves = [
            {"id": 1, "depends_on": [2], "tasks": ["1.1_setup"]},
            {"id": 2, "depends_on": [1], "tasks": ["2.1_build"]},
        ]
    elif warning_no_tasks:
        waves = [
            {"id": 1, "depends_on": [], "tasks": ["1.1_setup"]},
            {"id": 2, "depends_on": [1], "tasks": []},
        ]
    else:
        waves = [
            {"id": 1, "depends_on": [], "tasks": ["1.1_setup"]},
            {"id": 2, "depends_on": [1], "tasks": ["2.1_build"]},
        ]

    plan_data = {
        "name": name,
        "intent": "Hand authored test plan",
        "waves": waves,
    }
    (plans_dir / "plan.yml").write_text(yaml.dump(plan_data))

    # Write spec files
    w1_dir = plans_dir / "wave_1"
    w1_dir.mkdir(parents=True, exist_ok=True)
    if not cycle:
        (w1_dir / "1.1_setup_task.md").write_text("Setup task spec")

    if wave_gap:
        w3_dir = plans_dir / "wave_3"
        w3_dir.mkdir(parents=True, exist_ok=True)
        if not missing_spec:
            (w3_dir / "3.1_deploy_task.md").write_text("Deploy task spec")
    elif cycle:
        w2_dir = plans_dir / "wave_2"
        w2_dir.mkdir(parents=True, exist_ok=True)
        (w1_dir / "1.1_setup_task.md").write_text("Setup task spec")
        (w2_dir / "2.1_build_task.md").write_text("Build task spec")
    elif warning_no_tasks:
        w2_dir = plans_dir / "wave_2"
        w2_dir.mkdir(parents=True, exist_ok=True)
        (w2_dir / "2.1_build_task.md").write_text("Build task spec")
    else:
        w2_dir = plans_dir / "wave_2"
        w2_dir.mkdir(parents=True, exist_ok=True)
        if not missing_spec:
            (w2_dir / "2.1_build_task.md").write_text("Build task spec")

    return name


def test_plan_validate_passes_on_well_formed_plan(plan_env, capsys):
    """snodo plan validate passes on a well-formed hand-authored plan."""
    plan_name = _create_hand_authored_plan(plan_env, "well_formed")

    args = SimpleNamespace(plan_action="validate", name=plan_name, json_output=False)
    result = plan_command(args)
    assert result == 0

    out = capsys.readouterr().out
    assert f"Plan '{plan_name}' validated successfully." in out


def test_plan_validate_fails_on_missing_spec_file(plan_env, capsys):
    """snodo plan validate fails with error message on a missing spec file."""
    plan_name = _create_hand_authored_plan(plan_env, "missing_spec", missing_spec=True)

    args = SimpleNamespace(plan_action="validate", name=plan_name, json_output=False)
    result = plan_command(args)
    assert result == 1

    err = capsys.readouterr().err
    assert "Plan verification failed" in err
    assert "Missing spec: 2.1_build" in err


def test_plan_validate_fails_on_wave_gap(plan_env, capsys):
    """snodo plan validate fails with error message on a wave gap."""
    plan_name = _create_hand_authored_plan(plan_env, "wave_gap", wave_gap=True)

    args = SimpleNamespace(plan_action="validate", name=plan_name, json_output=False)
    result = plan_command(args)
    assert result == 1

    err = capsys.readouterr().err
    assert "Plan verification failed" in err
    assert "Wave-number gap detected" in err


def test_plan_validate_fails_on_dependency_cycle(plan_env, capsys):
    """snodo plan validate fails with error message on a dependency cycle."""
    plan_name = _create_hand_authored_plan(plan_env, "dep_cycle", cycle=True)

    args = SimpleNamespace(plan_action="validate", name=plan_name, json_output=False)
    result = plan_command(args)
    assert result == 1

    err = capsys.readouterr().err
    assert "Plan verification failed" in err
    assert "Wave dependency cycle detected" in err


def test_plan_validate_json_output_pass(plan_env, capsys):
    """snodo plan validate --json outputs machine-readable JSON for passing plan."""
    plan_name = _create_hand_authored_plan(plan_env, "json_pass")

    args = SimpleNamespace(plan_action="validate", name=plan_name, json_output=True)
    result = plan_command(args)
    assert result == 0

    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["schema"] == "snodo.plan_validate.v1"
    assert data["plan"] == plan_name
    assert data["passed"] is True
    assert data["errors"] == []


def test_plan_validate_json_output_fail(plan_env, capsys):
    """snodo plan validate --json outputs machine-readable JSON for failing plan."""
    plan_name = _create_hand_authored_plan(plan_env, "json_fail", missing_spec=True)

    args = SimpleNamespace(plan_action="validate", name=plan_name, json_output=True)
    result = plan_command(args)
    assert result == 1

    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["schema"] == "snodo.plan_validate.v1"
    assert data["plan"] == plan_name
    assert data["passed"] is False
    assert len(data["errors"]) > 0
    assert any("Missing spec" in e for e in data["errors"])


def test_plan_run_aborts_before_wave_1_on_verification_error(plan_env, capsys):
    """plan run verifies whole plan before executing wave 1; wave 3 spec failure aborts before wave 1 executes."""
    # Create plan with wave 1 (spec present) and wave 2 (spec missing)
    plans_dir = plan_env / ".snodo" / "plans" / "wave3_missing"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_data = {
        "name": "wave3_missing",
        "intent": "Aborts before wave 1",
        "waves": [
            {"id": 1, "depends_on": [], "tasks": ["1.1_first"]},
            {"id": 2, "depends_on": [1], "tasks": ["2.1_second_missing"]},
        ],
    }
    (plans_dir / "plan.yml").write_text(yaml.dump(plan_data))

    w1_dir = plans_dir / "wave_1"
    w1_dir.mkdir(parents=True, exist_ok=True)
    (w1_dir / "1.1_first_task.md").write_text("First task spec")

    # Do NOT create wave_2/2.1_second_missing_task.md

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml",
        model=None,
        plan="wave3_missing",
        wave=None,
        mock=True,
        interactive=False,
        no_isolation=True,
    )

    dispatched_tasks = []

    def fake_execute_task(args, protocol, task, model):
        dispatched_tasks.append(task.id)
        return 0

    with patch("snodo.cli.commands.run_cmd._execute_task", side_effect=fake_execute_task):
        result = _run_plan(args)

    assert result == 1
    # Wave 1 task MUST NOT have been dispatched
    assert len(dispatched_tasks) == 0

    err = capsys.readouterr().err
    assert "Missing spec: 2.1_second_missing" in err


def test_plan_run_proceeds_when_warnings_present(plan_env, capsys):
    """plan run prints warnings and proceeds with task execution when only warnings exist."""
    plan_name = _create_hand_authored_plan(plan_env, "warning_plan", warning_no_tasks=True)

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml",
        model=None,
        plan=plan_name,
        wave=None,
        mock=True,
        interactive=False,
        no_isolation=True,
    )

    dispatched_tasks = []

    def fake_execute_task(args, protocol, task, model):
        dispatched_tasks.append(task.id)
        return 0

    with patch("snodo.cli.commands.run_cmd._execute_task", side_effect=fake_execute_task):
        result = _run_plan(args)

    assert result == 0
    assert len(dispatched_tasks) == 1
    assert dispatched_tasks[0] == "1.1_setup"

    err = capsys.readouterr().err
    assert "Warnings:" in err
    assert "Wave 2 has no tasks" in err


def test_plan_validate_fails_on_non_integer_wave_id(plan_env, capsys):
    """A non-integer wave id is refused with its own message."""
    plans_dir = plan_env / ".snodo" / "plans" / "non_int_wave"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_data = {
        "name": "non_int_wave",
        "intent": "Test non integer wave id",
        "waves": [{"id": "abc", "tasks": ["1.1_setup"]}],
    }
    (plans_dir / "plan.yml").write_text(yaml.dump(plan_data))

    args = SimpleNamespace(plan_action="validate", name="non_int_wave", json_output=False)
    result = plan_command(args)
    assert result == 1

    err = capsys.readouterr().err
    assert "Wave id 'abc' is not an integer" in err


def test_plan_validate_fails_on_1a2_wave_id(plan_env, capsys):
    """Wave id '1a2' is refused rather than becoming wave 12 or wave 0 gap."""
    plans_dir = plan_env / ".snodo" / "plans" / "wave_1a2"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_data = {
        "name": "wave_1a2",
        "intent": "Test 1a2 wave id",
        "waves": [{"id": "1a2", "tasks": ["1.1_setup"]}],
    }
    (plans_dir / "plan.yml").write_text(yaml.dump(plan_data))

    args = SimpleNamespace(plan_action="validate", name="wave_1a2", json_output=False)
    result = plan_command(args)
    assert result == 1

    err = capsys.readouterr().err
    assert "Wave id '1a2' is not an integer" in err
    assert "12" not in err
    assert "Wave-number gap" not in err


def test_plan_validate_fails_on_non_integer_dependency(plan_env, capsys):
    """A dependency on a non-integer wave is refused with its own message."""
    plans_dir = plan_env / ".snodo" / "plans" / "non_int_dep"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_data = {
        "name": "non_int_dep",
        "intent": "Test non integer dependency",
        "waves": [
            {"id": 1, "depends_on": [], "tasks": ["1.1_setup"]},
            {"id": 2, "depends_on": ["xyz"], "tasks": ["2.1_build"]},
        ],
    }
    (plans_dir / "plan.yml").write_text(yaml.dump(plan_data))

    args = SimpleNamespace(plan_action="validate", name="non_int_dep", json_output=False)
    result = plan_command(args)
    assert result == 1

    err = capsys.readouterr().err
    assert "Wave 2 depends on non-integer wave 'xyz'" in err


def test_plan_validate_fails_on_corrupt_status_json(plan_env, capsys):
    """A corrupt status.json is refused with an error message."""
    plan_name = _create_hand_authored_plan(plan_env, "corrupt_status")
    status_file = plan_env / ".snodo" / "plans" / plan_name / "status.json"
    status_file.write_text("{corrupt status json content")

    args = SimpleNamespace(plan_action="validate", name=plan_name, json_output=False)
    result = plan_command(args)
    assert result == 1

    err = capsys.readouterr().err
    assert "Failed to parse status.json" in err

