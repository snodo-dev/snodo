"""Tests for environment preparation before task execution (Issue #26).

FILE: tests/infrastructure/test_environment.py
"""

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from snodo.compiler.models import ExecutionConfig, Mode, Protocol, Validator
from snodo.core.interfaces import Task
from snodo.engine.closure import run_to_closure
from snodo.engine.loop import build_protocol_graph
from snodo.infrastructure.environment import (
    EnvironmentPrepError,
    detect_prepare_command,
    prepare_environment,
)


@pytest.fixture
def temp_worktree(tmp_path):
    d = tmp_path / "worktree"
    d.mkdir()
    return d


class TestEnvironmentDetection:
    """Test ecosystem marker detection and skipping logic."""

    def test_node_lockfile_detection(self, temp_worktree):
        (temp_worktree / "package-lock.json").touch()
        cmd, guard = detect_prepare_command(temp_worktree)
        assert cmd == "npm ci"
        assert guard == "node_modules"

    def test_python_uv_detection(self, temp_worktree):
        (temp_worktree / "uv.lock").touch()
        cmd, guard = detect_prepare_command(temp_worktree)
        assert cmd == "uv sync"
        assert guard == ".venv"

    def test_cargo_detection(self, temp_worktree):
        (temp_worktree / "Cargo.toml").touch()
        cmd, guard = detect_prepare_command(temp_worktree)
        assert cmd == "cargo fetch"
        assert guard == "target"

    def test_go_detection(self, temp_worktree):
        (temp_worktree / "go.mod").touch()
        cmd, guard = detect_prepare_command(temp_worktree)
        assert cmd == "go mod download"
        assert guard == "vendor"

    def test_no_recognized_markers(self, temp_worktree):
        res = prepare_environment(temp_worktree)
        assert res.status == "skipped"
        assert res.reason == "no recognized markers"

    def test_already_installed_skips_auto_prep(self, temp_worktree):
        (temp_worktree / "package-lock.json").touch()
        (temp_worktree / "node_modules").mkdir()

        res = prepare_environment(temp_worktree)
        assert res.status == "skipped"
        assert "already installed" in res.reason


class TestExplicitProtocolOverride:
    """Test explicit protocol.execution.prepare_command configuration."""

    def test_protocol_declared_command_overrides_detection(self, temp_worktree):
        (temp_worktree / "package-lock.json").touch()
        protocol = Protocol(
            protocol_id="custom",
            name="Custom",
            modes=[Mode(mode_id="m", name="m", validators=["v"])],
            validators=[Validator(validator_id="v", validator_type="v")],
            initial_mode="m",
            execution=ExecutionConfig(prepare_command="make setup"),
        )

        executed_cmds = []

        def mock_runner(cmd, cwd):
            executed_cmds.append((cmd, cwd))
            return (0, "Done setup")

        res = prepare_environment(
            temp_worktree,
            protocol=protocol,
            run_command_fn=mock_runner,
        )
        assert res.status == "executed"
        assert res.command == "make setup"
        assert executed_cmds == [("make setup", str(temp_worktree))]

    def test_protocol_explicit_none_disables_prep(self, temp_worktree):
        (temp_worktree / "package-lock.json").touch()
        protocol = Protocol(
            protocol_id="custom",
            name="Custom",
            modes=[Mode(mode_id="m", name="m", validators=["v"])],
            validators=[Validator(validator_id="v", validator_type="v")],
            initial_mode="m",
            execution=ExecutionConfig(prepare_command="none"),
        )

        res = prepare_environment(temp_worktree, protocol=protocol)
        assert res.status == "skipped"
        assert "explicitly disabled" in res.reason


class TestEnvironmentPrepExecution:
    """Test worktree node preparation and error handling."""

    def test_node_lockfile_runs_prepare_command(self, temp_worktree):
        """A worktree with a Node lockfile and no node_modules runs prepare command."""
        (temp_worktree / "package-lock.json").touch()

        executed = []

        def runner(cmd, cwd):
            executed.append((cmd, cwd))
            return 0, "npm ci output"

        res = prepare_environment(temp_worktree, run_command_fn=runner)
        assert res.status == "executed"
        assert res.command == "npm ci"
        assert executed == [("npm ci", str(temp_worktree))]

    def test_shell_prepare_command_receives_environment_without_job_context(self, temp_worktree):
        from snodo.paths import JOB_CONTEXT_ENV_VARS

        with patch.dict("os.environ", {name: "job-value" for name in JOB_CONTEXT_ENV_VARS}):
            with patch("subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = ""
                run.return_value.stderr = ""
                prepare_environment(temp_worktree, explicit_command="true")

        child_env = run.call_args.kwargs["env"]
        assert not set(JOB_CONTEXT_ENV_VARS) & child_env.keys()

    def test_shell_mcp_prepare_command_receives_environment_without_job_context(self, temp_worktree):
        import sys

        from snodo.paths import JOB_CONTEXT_ENV_VARS
        from snodo.tools.shell import ShellMCP

        command = f'{sys.executable} -c "import os; print(\'|\'.join(k for k in {JOB_CONTEXT_ENV_VARS!r} if k in os.environ))"'
        with patch.dict("os.environ", {name: "job-value" for name in JOB_CONTEXT_ENV_VARS}):
            result = prepare_environment(
                temp_worktree,
                explicit_command=command,
                shell_mcp=ShellMCP(str(temp_worktree)),
            )

        assert result.status == "executed"
        assert result.output.strip() == ""

    def test_failed_install_raises_environment_prep_error(self, temp_worktree):
        (temp_worktree / "package-lock.json").touch()

        def runner(cmd, cwd):
            return 127, "npm: command not found"

        with pytest.raises(EnvironmentPrepError) as exc_info:
            prepare_environment(temp_worktree, run_command_fn=runner)

        err = exc_info.value
        assert err.command == "npm ci"
        assert err.exit_code == 127
        assert "npm: command not found" in err.output


class TestEngineIntegrationOperationalFault:
    """Test engine handling of environment preparation faults."""

    def test_failed_install_is_operational_fault_no_recovery(self, temp_worktree):
        """A failed install halts immediately with validator_error and spawns no recovery subtask."""
        (temp_worktree / "package-lock.json").touch()

        # Create protocol with prepare_command that will fail
        protocol = Protocol(
            protocol_id="test_prep_fail",
            name="Test Prep Fail",
            version="1.0.0",
            modes=[
                Mode(
                    mode_id="producer",
                    name="Producer",
                    tools=["edit"],
                    validators=["v1"],
                )
            ],
            validators=[
                Validator(
                    validator_id="v1",
                    validator_type="llm",
                    criteria=["Spec clear"],
                )
            ],
            initial_mode="producer",
            execution=ExecutionConfig(prepare_command="bad_install_command_fails"),
        )

        mock_shell = MagicMock()
        mock_shell.run_command.return_value = (127, "bad_install_command_fails: command not found")

        # 1. Direct unit check on prepare_environment
        with pytest.raises(EnvironmentPrepError) as exc_info:
            prepare_environment(temp_worktree, protocol=protocol, shell_mcp=mock_shell)
        assert exc_info.value.exit_code == 127
        assert "bad_install_command_fails" in exc_info.value.command

        # 2. Integration check with validator_fn injected to prevent unrelated LLM auth errors
        graph = build_protocol_graph(
            protocol=protocol,
            use_mock_coder=True,
            project_root=str(temp_worktree),
            worktree_path=str(temp_worktree),
            git_mcp=MagicMock(),
            shell_mcp=mock_shell,
            validator_fn=lambda task, validators, shell, **kwargs: [],
        ).compile()

        task = Task(id="task_prep_fail", spec="Some task")
        final_state, tree = run_to_closure(graph, task, mode="producer")

        # Must report validator_error operational fault
        assert tree.outcome == "validator_error"
        assert tree.spawned_subtasks == 0
        assert len(tree.subtasks) == 0  # No recovery subtasks spawned!

        payload = tree.halt_payload
        assert payload is not None
        assert payload["status"] == "blocked"
        assert payload["halt_type"] == "validator_error"
        assert "bad_install_command_fails" in payload["reason"]
