"""Tests for the sandbox system.

FILE: tests/sandbox/test_sandbox.py (Task 5.4)

Unit tests (mock Docker), CLI integration tests, and local sandbox tests.
"""

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

from snodo.sandbox.base import Sandbox, SandboxResult, SandboxConfig, SandboxError
from snodo.sandbox.local_sandbox import LocalSandbox
from snodo.sandbox.docker_sandbox import DockerSandbox
from snodo.sandbox import create_sandbox


# === Fixtures ===

@pytest.fixture
def temp_project():
    """Create a temporary project with .snodo/ directory."""
    temp_dir = tempfile.mkdtemp()
    snodo_dir = Path(temp_dir) / ".snodo"
    snodo_dir.mkdir()

    # Write a minimal protocol file
    protocol_file = snodo_dir / "protocol.yml"
    protocol_file.write_text(
        'protocol_id: "test"\n'
        'name: "Test Protocol"\n'
        'version: "1.0.0"\n'
        'modes:\n'
        '  - mode_id: "producer"\n'
        '    name: "Producer"\n'
        '    tools: ["edit"]\n'
        '    validators: ["security"]\n'
        '    transitions: {}\n'
        'validators:\n'
        '  - validator_id: "security"\n'
        '    validator_type: "security"\n'
        '    evaluation_phase: "pre_execute"\n'
        '    criteria: ["check"]\n'
        'disagreement_policy: "unanimous"\n'
        'initial_mode: "producer"\n'
        'global_constraints: []\n'
    )

    original_cwd = Path.cwd()
    try:
        os.chdir(temp_dir)
        yield Path(temp_dir)
    finally:
        os.chdir(original_cwd)


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)


# === SandboxResult Tests ===

class TestSandboxResult:
    """Tests for SandboxResult dataclass."""

    def test_default_values(self):
        result = SandboxResult(exit_code=0)
        assert result.exit_code == 0
        assert result.stdout == ""
        assert result.stderr == ""
        assert result.duration == 0.0
        assert result.sandbox_type == "local"
        assert result.container_id is None

    def test_custom_values(self):
        result = SandboxResult(
            exit_code=1,
            stdout="output",
            stderr="error",
            duration=5.2,
            sandbox_type="docker",
            container_id="abc123",
        )
        assert result.exit_code == 1
        assert result.stdout == "output"
        assert result.stderr == "error"
        assert result.duration == 5.2
        assert result.sandbox_type == "docker"
        assert result.container_id == "abc123"


class TestSandboxConfig:
    """Tests for SandboxConfig dataclass."""

    def test_defaults(self):
        config = SandboxConfig()
        assert config.image == "snodo-worker:latest"
        assert config.network == "none"
        assert config.memory_limit == "2g"
        assert config.cpu_limit == 2.0
        assert config.timeout is None
        assert config.env == {}
        assert config.mounts == {}

    def test_custom(self):
        config = SandboxConfig(
            image="custom:v1",
            network="bridge",
            memory_limit="4g",
            cpu_limit=4.0,
            timeout=300,
            env={"KEY": "val"},
        )
        assert config.image == "custom:v1"
        assert config.network == "bridge"
        assert config.memory_limit == "4g"
        assert config.cpu_limit == 4.0
        assert config.timeout == 300
        assert config.env == {"KEY": "val"}


# === LocalSandbox Tests ===

class TestLocalSandbox:
    """Tests for LocalSandbox."""

    def test_is_always_available(self):
        sandbox = LocalSandbox()
        assert sandbox.is_available() is True

    def test_run_simple_command(self, temp_workspace):
        sandbox = LocalSandbox()
        result = sandbox.run_task(
            ["echo", "hello world"],
            temp_workspace,
        )
        assert result.exit_code == 0
        assert "hello world" in result.stdout
        assert result.sandbox_type == "local"
        assert result.duration > 0

    def test_run_failing_command(self, temp_workspace):
        sandbox = LocalSandbox()
        result = sandbox.run_task(
            ["false"],
            temp_workspace,
        )
        assert result.exit_code != 0
        assert result.sandbox_type == "local"

    def test_run_with_timeout(self, temp_workspace):
        sandbox = LocalSandbox()
        config = SandboxConfig(timeout=0.1)
        result = sandbox.run_task(
            ["sleep", "10"],
            temp_workspace,
            config=config,
        )
        assert result.exit_code == 124
        assert "timed out" in result.stderr

    def test_run_with_env(self, temp_workspace):
        sandbox = LocalSandbox()
        config = SandboxConfig(env={"TEST_VAR": "sandbox_value"})
        result = sandbox.run_task(
            ["sh", "-c", "echo $TEST_VAR"],
            temp_workspace,
            config=config,
        )
        assert result.exit_code == 0
        assert "sandbox_value" in result.stdout

    def test_run_captures_stderr(self, temp_workspace):
        sandbox = LocalSandbox()
        result = sandbox.run_task(
            ["sh", "-c", "echo err >&2"],
            temp_workspace,
        )
        assert "err" in result.stderr

    def test_run_uses_workspace_as_cwd(self, temp_workspace):
        sandbox = LocalSandbox()
        result = sandbox.run_task(
            ["pwd"],
            temp_workspace,
        )
        assert result.exit_code == 0
        # Resolve symlinks for macOS /private/var
        actual = Path(result.stdout.strip()).resolve()
        expected = temp_workspace.resolve()
        assert actual == expected

    def test_command_not_found_raises(self, temp_workspace):
        sandbox = LocalSandbox()
        with pytest.raises(SandboxError, match="Command not found"):
            sandbox.run_task(
                ["nonexistent_command_xyz"],
                temp_workspace,
            )

    def test_cleanup_is_noop(self):
        sandbox = LocalSandbox()
        sandbox.cleanup()  # Should not raise


# === DockerSandbox Tests (Mocked) ===

class TestDockerSandbox:
    """Tests for DockerSandbox with mocked Docker client."""

    @patch("docker.from_env")
    def test_is_available_true(self, mock_from_env):
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_from_env.return_value = mock_client

        sandbox = DockerSandbox()
        assert sandbox.is_available() is True
        mock_client.ping.assert_called_once()

    @patch("docker.from_env")
    def test_is_available_false(self, mock_from_env):
        mock_client = MagicMock()
        mock_client.ping.side_effect = Exception("Connection refused")
        mock_from_env.return_value = mock_client

        sandbox = DockerSandbox()
        assert sandbox.is_available() is False

    @patch("docker.from_env")
    def test_image_exists_true(self, mock_from_env):
        mock_client = MagicMock()
        mock_client.images.get.return_value = MagicMock()
        mock_from_env.return_value = mock_client

        sandbox = DockerSandbox()
        assert sandbox.image_exists() is True

    @patch("docker.from_env")
    def test_image_exists_false(self, mock_from_env):
        mock_client = MagicMock()
        mock_client.images.get.side_effect = Exception("not found")
        mock_from_env.return_value = mock_client

        sandbox = DockerSandbox()
        assert sandbox.image_exists() is False

    @patch("docker.from_env")
    def test_run_task_success(self, mock_from_env):
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client

        # Mock container
        mock_container = MagicMock()
        mock_container.id = "abc123def456"
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.side_effect = [
            b"task output\n",  # stdout
            b"",  # stderr
        ]
        mock_client.containers.run.return_value = mock_container

        sandbox = DockerSandbox()
        workspace = Path("/tmp/test_workspace")
        result = sandbox.run_task(
            ["snodo", "run", "test task"],
            workspace,
        )

        assert result.exit_code == 0
        assert result.stdout == "task output\n"
        assert result.stderr == ""
        assert result.sandbox_type == "docker"
        assert result.container_id == "abc123def456"

        # Verify container was run with correct args
        mock_client.containers.run.assert_called_once()
        call_kwargs = mock_client.containers.run.call_args[1]
        assert call_kwargs["network_mode"] == "none"
        assert call_kwargs["mem_limit"] == "2g"
        assert call_kwargs["detach"] is True

        # Verify container was removed
        mock_container.remove.assert_called_once_with(force=True)

    @patch("docker.from_env")
    def test_run_task_failure(self, mock_from_env):
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client

        mock_container = MagicMock()
        mock_container.id = "fail123"
        mock_container.wait.return_value = {"StatusCode": 1}
        mock_container.logs.side_effect = [
            b"",  # stdout
            b"Error: task failed\n",  # stderr
        ]
        mock_client.containers.run.return_value = mock_container

        sandbox = DockerSandbox()
        result = sandbox.run_task(
            ["snodo", "run", "failing task"],
            Path("/tmp/workspace"),
        )

        assert result.exit_code == 1
        assert result.stderr == "Error: task failed\n"

    @patch("docker.from_env")
    def test_run_task_with_config(self, mock_from_env):
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client

        mock_container = MagicMock()
        mock_container.id = "cfg123"
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.side_effect = [b"", b""]
        mock_client.containers.run.return_value = mock_container

        sandbox = DockerSandbox()
        config = SandboxConfig(
            network="bridge",
            memory_limit="4g",
            cpu_limit=4.0,
            env={"API_KEY": "secret"},
        )
        sandbox.run_task(
            ["snodo", "run", "task"],
            Path("/tmp/workspace"),
            config=config,
        )

        call_kwargs = mock_client.containers.run.call_args[1]
        assert call_kwargs["network_mode"] == "bridge"
        assert call_kwargs["mem_limit"] == "4g"
        assert call_kwargs["cpu_quota"] == 400000  # 4.0 * 100000
        assert call_kwargs["environment"] == {"API_KEY": "secret"}

    @patch("docker.from_env")
    def test_run_task_exception_returns_result(self, mock_from_env):
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client

        mock_container = MagicMock()
        mock_container.id = "err123"
        mock_container.wait.side_effect = Exception("timeout")
        mock_container.logs.side_effect = [b"partial", b"error output"]
        mock_client.containers.run.return_value = mock_container

        sandbox = DockerSandbox()
        result = sandbox.run_task(
            ["snodo", "run", "task"],
            Path("/tmp/workspace"),
        )

        assert result.exit_code == 1
        assert result.sandbox_type == "docker"
        mock_container.remove.assert_called_once_with(force=True)

    @patch("docker.from_env")
    def test_build_image_success(self, mock_from_env):
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client

        mock_image = MagicMock()
        mock_image.id = "sha256:abc123"
        mock_client.images.build.return_value = (mock_image, [])

        sandbox = DockerSandbox()
        dockerfile = Path("/tmp/project/Dockerfile.worker")

        with patch.object(Path, "parent", new_callable=PropertyMock, return_value=Path("/tmp/project")):
            with patch.object(Path, "name", new_callable=PropertyMock, return_value="Dockerfile.worker"):
                image_id = sandbox.build_image(dockerfile)

        assert image_id == "sha256:abc123"

    @patch("docker.from_env")
    def test_build_image_failure(self, mock_from_env):
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.images.build.side_effect = Exception("Build failed")

        sandbox = DockerSandbox()
        with pytest.raises(SandboxError, match="Failed to build image"):
            sandbox.build_image(Path("/tmp/Dockerfile.worker"))

    @patch("docker.from_env")
    def test_cleanup_removes_containers(self, mock_from_env):
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client

        sandbox = DockerSandbox()
        sandbox._client = mock_client
        sandbox._containers = ["c1", "c2"]

        mock_c1 = MagicMock()
        mock_c2 = MagicMock()
        mock_client.containers.get.side_effect = [mock_c1, mock_c2]

        sandbox.cleanup()

        assert mock_c1.remove.called
        assert mock_c2.remove.called
        assert sandbox._containers == []

    def test_docker_not_installed(self):
        """Should raise SandboxError if docker package missing."""
        sandbox = DockerSandbox()
        sandbox._client = None  # Force re-init

        with patch.dict(sys.modules, {"docker": None}):
            with patch("builtins.__import__", side_effect=ImportError("No module named 'docker'")):
                # Creating a new sandbox and accessing client should fail
                new_sandbox = DockerSandbox()
                new_sandbox._client = None
                with pytest.raises(SandboxError, match="docker package not installed"):
                    _ = new_sandbox.client


# === Factory Tests ===

class TestCreateSandbox:
    """Tests for the create_sandbox factory function."""

    def test_create_local(self):
        sandbox = create_sandbox("local")
        assert isinstance(sandbox, LocalSandbox)
        assert sandbox.is_available()

    @patch("docker.from_env")
    def test_create_docker_available(self, mock_from_env):
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_from_env.return_value = mock_client

        sandbox = create_sandbox("docker")
        assert isinstance(sandbox, DockerSandbox)

    @patch("docker.from_env")
    def test_create_docker_unavailable(self, mock_from_env):
        mock_client = MagicMock()
        mock_client.ping.side_effect = Exception("not running")
        mock_from_env.return_value = mock_client

        with pytest.raises(SandboxError, match="Docker is not available"):
            create_sandbox("docker")

    def test_create_unknown_type(self):
        with pytest.raises(SandboxError, match="Unknown sandbox type"):
            create_sandbox("kubernetes")


# === CLI Command Tests ===

class TestSandboxCommand:
    """Tests for sandbox CLI commands."""

    @patch("docker.from_env")
    def test_sandbox_status_docker_available(self, mock_from_env, capsys):
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.images.get.return_value = MagicMock()
        mock_client.info.return_value = {
            "ServerVersion": "24.0.0",
            "OperatingSystem": "Docker Desktop",
        }
        mock_from_env.return_value = mock_client

        from snodo.cli.commands.sandbox_cmd import sandbox_command
        args = SimpleNamespace(sandbox_action="status")
        result = sandbox_command(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "available" in captured.out
        assert "ready" in captured.out

    @patch("docker.from_env")
    def test_sandbox_status_docker_unavailable(self, mock_from_env, capsys):
        mock_from_env.side_effect = Exception("not running")

        from snodo.cli.commands.sandbox_cmd import sandbox_command
        args = SimpleNamespace(sandbox_action="status")
        result = sandbox_command(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "not available" in captured.out

    def test_sandbox_build_no_dockerfile(self, temp_project, capsys):
        """Should error when Dockerfile.worker not found."""
        from snodo.cli.commands.sandbox_cmd import _sandbox_build

        # Ensure no Dockerfile.worker in temp_project
        df = temp_project / "Dockerfile.worker"
        if df.exists():
            df.unlink()

        args = SimpleNamespace(sandbox_action="build", tag=None)

        # Patch Path(__file__) to point to temp_project so pkg fallback also fails
        with patch("snodo.cli.commands.sandbox_cmd.__file__",
                   str(temp_project / "snodo" / "cli" / "commands" / "sandbox_cmd.py")):
            result = _sandbox_build(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "not found" in captured.err

    @patch("docker.from_env")
    def test_sandbox_build_success(self, mock_from_env, temp_project, capsys):
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_image = MagicMock()
        mock_image.id = "sha256:built123"
        mock_client.images.build.return_value = (mock_image, [])
        mock_from_env.return_value = mock_client

        # Create Dockerfile.worker in temp project
        dockerfile = temp_project / "Dockerfile.worker"
        dockerfile.write_text("FROM python:3.13-slim\n")

        from snodo.cli.commands.sandbox_cmd import sandbox_command
        args = SimpleNamespace(sandbox_action="build", tag=None)
        result = sandbox_command(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "Successfully built" in captured.out

    def test_sandbox_unknown_action(self, capsys):
        from snodo.cli.commands.sandbox_cmd import sandbox_command
        args = SimpleNamespace(sandbox_action="unknown")
        result = sandbox_command(args)
        assert result == 1


# === CLI Integration Tests ===

class TestSandboxCLI:
    """Tests for the 'snodo sandbox' CLI command via main()."""

    @patch("docker.from_env")
    def test_cli_sandbox_status(self, mock_from_env, capsys):
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.images.get.side_effect = Exception("not found")
        mock_client.info.return_value = {"ServerVersion": "24.0", "OperatingSystem": "Linux"}
        mock_from_env.return_value = mock_client

        from snodo.cli.main import main
        result = main(argv=["sandbox", "status"])
        assert result == 0

    @patch("docker.from_env")
    def test_cli_run_with_sandbox_option(self, mock_from_env, temp_project, capsys):
        """snodo run with --sandbox docker should attempt docker execution."""
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.images.get.side_effect = Exception("not found")
        mock_from_env.return_value = mock_client

        from snodo.cli.main import main
        result = main(argv=["run", "test task", "--mock", "--sandbox", "docker"])

        # Should fail because image not built
        assert result == 1
        captured = capsys.readouterr()
        assert "not built" in captured.err

    def test_cli_run_sandbox_local_default(self, temp_project, capsys):
        """snodo run with --sandbox local should use normal execution."""
        from snodo.cli.main import main

        # This will fail at execution stage but should get past sandbox routing
        result = main(argv=["run", "test task", "--mock", "--sandbox", "local"])
        # Should proceed to normal execution (may fail due to graph setup, that's OK)
        # The key assertion is that it doesn't error with "Docker not available"
        captured = capsys.readouterr()
        assert "Docker" not in captured.err or "not available" not in captured.err


# === Run Command Sandbox Integration ===

class TestRunInSandbox:
    """Tests for _run_in_sandbox in run_cmd."""

    @patch("docker.from_env")
    def test_fallback_when_docker_unavailable(self, mock_from_env, temp_project, capsys):
        """Should fall back to local when Docker is unavailable."""
        mock_client = MagicMock()
        mock_client.ping.side_effect = Exception("not running")
        mock_from_env.return_value = mock_client

        from snodo.cli.commands.run_cmd import _run_in_sandbox
        args = SimpleNamespace(
            description="test",
            protocol=".snodo/protocol.yml",
            model=None,
            mock=True,
            verbose=False,
            from_pr=None,
            sandbox="docker",
            background=False,
            plan=None,
            wave=None,
            interactive=False,
        )

        # _run_in_sandbox should detect unavailable docker and recurse with sandbox=local
        # This will proceed to normal execution
        result = _run_in_sandbox(args)
        captured = capsys.readouterr()
        assert "falling back to local" in captured.err

    @patch("docker.from_env")
    def test_error_when_image_not_built(self, mock_from_env, capsys):
        """Should error when image doesn't exist."""
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.images.get.side_effect = Exception("not found")
        mock_from_env.return_value = mock_client

        from snodo.cli.commands.run_cmd import _run_in_sandbox
        args = SimpleNamespace(
            description="test",
            protocol=".snodo/protocol.yml",
            model=None,
            mock=True,
            verbose=False,
            from_pr=None,
            sandbox="docker",
        )

        result = _run_in_sandbox(args)
        assert result == 1
        captured = capsys.readouterr()
        assert "not built" in captured.err

    @patch("docker.from_env")
    def test_successful_docker_execution(self, mock_from_env, capsys):
        """Should run task in Docker container when available."""
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.images.get.return_value = MagicMock()
        mock_from_env.return_value = mock_client

        mock_container = MagicMock()
        mock_container.id = "run123abc"
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.side_effect = [
            b"Task completed!\n",
            b"",
        ]
        mock_client.containers.run.return_value = mock_container

        from snodo.cli.commands.run_cmd import _run_in_sandbox
        args = SimpleNamespace(
            description="implement feature",
            protocol=".snodo/protocol.yml",
            model=None,
            mock=True,
            verbose=False,
            from_pr=None,
            sandbox="docker",
        )

        with patch("snodo.cli.commands.run_cmd.ConfigManager") as mock_cfg:
            mock_cfg.return_value.get_model.return_value = "mock"
            mock_cfg.return_value.get_key_for_model.return_value = None

            result = _run_in_sandbox(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "Docker sandbox" in captured.out
        assert "Task completed!" in captured.out


# === Module Import Tests ===

class TestImports:
    """Tests that sandbox modules import correctly."""

    def test_import_package(self):
        from snodo.sandbox import Sandbox, SandboxResult
        from snodo.sandbox import DockerSandbox, LocalSandbox, create_sandbox
        assert Sandbox is not None
        assert SandboxResult is not None
        assert DockerSandbox is not None
        assert LocalSandbox is not None
        assert create_sandbox is not None

    def test_import_base(self):
        from snodo.sandbox.base import Sandbox, SandboxConfig
        assert Sandbox is not None
        assert SandboxConfig is not None

    def test_import_sandbox_cmd(self):
        from snodo.cli.commands.sandbox_cmd import sandbox_command
        assert callable(sandbox_command)

    def test_sandbox_is_abstract(self):
        """Sandbox ABC cannot be instantiated."""
        with pytest.raises(TypeError):
            Sandbox()
