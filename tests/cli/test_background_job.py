"""Behavioral tests for background job execution helpers."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from snodo.jobs import JobError

from snodo.cli.commands.background_job import _submit_background_job


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
    """_submit_background_job exits 1 when the protocol file does not exist."""
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
    """_submit_background_job submits a job and prints helper commands."""
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
