"""Behavioral tests for sandbox and background job execution helpers (snodo/cli/commands/sandbox_run.py).

FILE: tests/cli/test_sandbox_run.py
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from snodo.jobs import JobError
from snodo.sandbox import SandboxError

from snodo.cli.commands.sandbox_run import (
    _build_sandbox_command,
    _build_sandbox_env,
    _print_sandbox_result,
    _run_in_sandbox,
    _submit_background_job,
)

# ============================================================================
# 1. Helper Functions Unit Tests
# ============================================================================

def test_build_sandbox_command():
    """_build_sandbox_command builds CLI argument list for container execution."""
    args = SimpleNamespace(
        description="Implement user auth",
        protocol=".snodo/protocol.yml",
        model="gpt-4o",
        mock=True,
        verbose=True,
        from_pr=42,
    )
    cmd = _build_sandbox_command(args)
    assert cmd == [
        "snodo",
        "run",
        "Implement user auth",
        "--protocol",
        ".snodo/protocol.yml",
        "--model",
        "gpt-4o",
        "--mock",
        "--verbose",
        "--from-pr",
        "42",
    ]


def test_build_sandbox_env(monkeypatch):
    """_build_sandbox_env constructs API key environment map for sandbox container."""
    mock_mgr = MagicMock()
    mock_mgr.get_key_for_model.return_value = "sk-test-key-123"
    monkeypatch.setattr("snodo.config.ConfigManager._provider_for_model", lambda m: "openai")

    env = _build_sandbox_env(mock_mgr, "openai/gpt-4o")
    assert env.get("OPENAI_API_KEY") == "sk-test-key-123"

    # Missing API key returns empty dict
    mock_mgr.get_key_for_model.return_value = None
    assert _build_sandbox_env(mock_mgr, "openai/gpt-4o") == {}


def test_print_sandbox_result(capsys):
    """_print_sandbox_result formats sandbox output, container ID, and exit code."""
    result = SimpleNamespace(
        stdout="Task executed\n",
        stderr="Minor warning\n",
        container_id="c_12345",
        duration=3.5,
        exit_code=0,
    )
    _print_sandbox_result(result, "snodo-worker:latest", None)

    captured = capsys.readouterr()
    assert "Task executed" in captured.out
    assert "Container: c_12345" in captured.out
    assert "Duration: 3.5s" in captured.out
    assert "Exit code: 0" in captured.out
    assert "Minor warning" in captured.err


# ============================================================================
# 2. _run_in_sandbox Execution & Failure Path Tests
# ============================================================================

def test_run_in_sandbox_docker_unavailable_fallback(capsys, monkeypatch):
    """_run_in_sandbox falls back to local execution when Docker is unavailable."""
    mock_sandbox = MagicMock()
    mock_sandbox.is_available.return_value = False

    monkeypatch.setattr("snodo.sandbox.DockerSandbox", lambda: mock_sandbox)
    monkeypatch.setattr("snodo.cli.commands.run_cmd.run_command", lambda args: 42)

    args = SimpleNamespace(sandbox="docker")
    res = _run_in_sandbox(args)

    assert res == 42
    assert args.sandbox == "local"
    err = capsys.readouterr().err
    assert "Warning: Docker not available, falling back to local execution" in err


def test_run_in_sandbox_image_missing(capsys, monkeypatch):
    """_run_in_sandbox exits non-zero (1) when worker image is not built."""
    mock_sandbox = MagicMock()
    mock_sandbox.is_available.return_value = True
    mock_sandbox.image_exists.return_value = False

    monkeypatch.setattr("snodo.sandbox.DockerSandbox", lambda: mock_sandbox)

    args = SimpleNamespace(sandbox="docker")
    res = _run_in_sandbox(args)

    assert res == 1
    err = capsys.readouterr().err
    assert "Error: snodo-worker image not built" in err


def test_run_in_sandbox_happy_path(capsys, tmp_path, monkeypatch):
    """_run_in_sandbox executes task in Docker sandbox container and returns exit code."""
    mock_sandbox = MagicMock()
    mock_sandbox.is_available.return_value = True
    mock_sandbox.image_exists.return_value = True
    mock_sandbox._image = "snodo-worker:latest"

    mock_result = SimpleNamespace(stdout="Passed\n", stderr="", container_id="c_99", duration=1.2, exit_code=0)
    mock_sandbox.run_task.return_value = mock_result

    monkeypatch.setattr("snodo.sandbox.DockerSandbox", lambda: mock_sandbox)
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.config.ConfigManager.get_model", lambda self: "mock-model")
    monkeypatch.setattr("snodo.config.ConfigManager.get_key_for_model", lambda self, m: None)

    args = SimpleNamespace(
        description="Task in container",
        protocol=".snodo/protocol.yml",
        model=None,
    )
    res = _run_in_sandbox(args)

    assert res == 0
    out = capsys.readouterr().out
    assert "Running in Docker sandbox..." in out
    assert "Container: c_99" in out


def test_run_in_sandbox_error_raised(capsys, tmp_path, monkeypatch):
    """_run_in_sandbox handles SandboxError raised during container execution."""
    mock_sandbox = MagicMock()
    mock_sandbox.is_available.return_value = True
    mock_sandbox.image_exists.return_value = True
    mock_sandbox._image = "snodo-worker:latest"
    mock_sandbox.run_task.side_effect = SandboxError("Container crashed")

    monkeypatch.setattr("snodo.sandbox.DockerSandbox", lambda: mock_sandbox)
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.config.ConfigManager.get_model", lambda self: "mock-model")

    args = SimpleNamespace(
        description="Task crashing",
        protocol=".snodo/protocol.yml",
        model="mock-model",
    )
    res = _run_in_sandbox(args)

    assert res == 1
    err = capsys.readouterr().err
    assert "Error: Container crashed" in err


# ============================================================================
# 3. _submit_background_job Failure Paths & Happy Path
# ============================================================================

def test_submit_background_job_plan_conflict(capsys):
    """_submit_background_job exits 1 when --plan and --background are combined."""
    args = SimpleNamespace(plan="my_plan", background=True)
    res = _submit_background_job(args)
    assert res == 1
    err = capsys.readouterr().err
    assert "Error: --plan and --background cannot be used together" in err


def test_submit_background_job_missing_description(capsys):
    """_submit_background_job exits 1 when description is missing."""
    args = SimpleNamespace(plan=None, description=None, background=True)
    res = _submit_background_job(args)
    assert res == 1
    err = capsys.readouterr().err
    assert "Error: task description required for background jobs" in err


def test_submit_background_job_protocol_missing(capsys, tmp_path):
    """_submit_background_job exits 1 when protocol file does not exist."""
    args = SimpleNamespace(
        plan=None,
        description="Background task",
        protocol=str(tmp_path / "nonexistent_protocol.yml"),
        background=True,
    )
    res = _submit_background_job(args)
    assert res == 1
    err = capsys.readouterr().err
    assert "Error: Protocol file not found" in err


def test_submit_background_job_manager_error(capsys, tmp_path, monkeypatch):
    """_submit_background_job handles JobError raised by JobManager."""
    protocol_file = tmp_path / "protocol.yml"
    protocol_file.write_text("name: test")

    mock_job_mgr = MagicMock()
    mock_job_mgr.submit.side_effect = JobError("Max background jobs reached")

    monkeypatch.setattr("snodo.jobs.JobManager", lambda root: mock_job_mgr)
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.config.ConfigManager.get_model", lambda self: "mock-model")

    args = SimpleNamespace(
        plan=None,
        description="Background task",
        protocol=str(protocol_file),
        model="mock-model",
        mock=True,
        verbose=False,
        from_pr=None,
    )
    res = _submit_background_job(args)
    assert res == 1
    err = capsys.readouterr().err
    assert "Error: Max background jobs reached" in err


def test_submit_background_job_happy_path(capsys, tmp_path, monkeypatch):
    """_submit_background_job submits job and prints job ID with helper commands."""
    protocol_file = tmp_path / "protocol.yml"
    protocol_file.write_text("name: test")

    mock_job_mgr = MagicMock()
    mock_job_mgr.submit.return_value = "j_sub12345"

    monkeypatch.setattr("snodo.jobs.JobManager", lambda root: mock_job_mgr)
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.config.ConfigManager.get_model", lambda self: "mock-model")

    args = SimpleNamespace(
        plan=None,
        description="Background task description",
        protocol=str(protocol_file),
        model="mock-model",
        mock=True,
        verbose=False,
        from_pr=None,
    )
    res = _submit_background_job(args)
    assert res == 0

    out = capsys.readouterr().out
    assert "Job submitted: j_sub12345" in out
    assert "snodo job status j_sub12345" in out
    assert "snodo job logs j_sub12345" in out
    assert "snodo job wait j_sub12345" in out
    assert "snodo meta j_sub12345" in out


def test_submit_background_job_preserves_coder_and_mode(tmp_path, monkeypatch):
    """_submit_background_job includes coder and mode in task_args."""
    protocol_file = tmp_path / "protocol.yml"
    protocol_file.write_text("name: test")

    mock_job_mgr = MagicMock()
    captured_args = {}
    mock_job_mgr.submit.side_effect = lambda a: captured_args.update(a) or "j_123"

    monkeypatch.setattr("snodo.jobs.JobManager", lambda root: mock_job_mgr)
    monkeypatch.setattr("snodo.infrastructure.paths.require_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.config.ConfigManager.get_model", lambda self: "mock-model")

    args = SimpleNamespace(
        plan=None,
        description="Background task description",
        protocol=str(protocol_file),
        model="mock-model",
        coder="opencode-cli",
        mode="producer",
        mock=False,
        verbose=False,
        from_pr=None,
    )
    res = _submit_background_job(args)
    assert res == 0
    assert captured_args.get("coder") == "opencode-cli"
    assert captured_args.get("mode") == "producer"


def test_build_sandbox_command_with_coder_and_mode():
    """_build_sandbox_command includes --coder and --mode flags."""
    args = SimpleNamespace(
        description="Implement user auth",
        protocol=".snodo/protocol.yml",
        model="gpt-4o",
        coder="opencode-cli",
        mode="reviewer",
        mock=False,
        verbose=False,
        from_pr=None,
    )
    cmd = _build_sandbox_command(args)
    assert "--coder" in cmd
    assert cmd[cmd.index("--coder") + 1] == "opencode-cli"
    assert "--mode" in cmd
    assert cmd[cmd.index("--mode") + 1] == "reviewer"
