"""Operator-facing behavior tests for ``snodo job`` filesystem records."""

import json
import os
from pathlib import Path

import pytest

from snodo.cli.main import main


def _write_job(project: Path, job_id: str, *, status: str, created_at: float,
               exit_code=None, task=None, started_at=None, completed_at=None,
               stdout="", stderr="") -> Path:
    job_dir = project / ".snodo" / "jobs" / job_id
    job_dir.mkdir(parents=True)
    state = {
        "status": status,
        "pid": None,
        "created_at": created_at,
        "started_at": started_at,
        "completed_at": completed_at,
        "exit_code": exit_code,
    }
    (job_dir / "state.json").write_text(json.dumps(state))
    (job_dir / "task.json").write_text(json.dumps(task or {
        "description": "Inspect the recorded job behavior",
        "task_id": job_id,
    }))
    (job_dir / "stdout.log").write_text(stdout)
    (job_dir / "stderr.log").write_text(stderr)
    return job_dir


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / ".snodo" / "jobs").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_job_list_empty_is_explicit(project, capsys):
    assert main(["job", "list"]) == 0
    assert capsys.readouterr().out.strip() == "No jobs found."


def test_job_list_is_newest_first_and_bounded_to_job_summaries(project, capsys):
    _write_job(project, "j_old", status="completed", created_at=10,
               exit_code=0, started_at=11, completed_at=12,
               task={"description": "Old recorded task", "task_id": "old"})
    _write_job(project, "j_new", status="failed", created_at=20,
               exit_code=1, started_at=21, completed_at=22,
               task={"description": "New recorded task\n" + "detail\n" * 20,
                     "task_id": "new"})

    assert main(["job", "list"]) == 0
    output = capsys.readouterr().out
    assert output.index("j_new") < output.index("j_old")
    assert "New recorded task" in output
    assert "detail" not in output
    assert "task: new" in output


@pytest.mark.parametrize("status", [
    "queued", "running", "completed", "failed", "cancelled", "unmerged",
])
def test_job_status_displays_every_supported_status(project, capsys, status):
    _write_job(project, "j_status", status=status, created_at=10,
               exit_code=0 if status in {"completed", "unmerged"} else None,
               started_at=11 if status != "queued" else None,
               completed_at=12 if status in {"completed", "failed", "cancelled", "unmerged"} else None)

    assert main(["job", "status", "j_status"]) == 0
    assert f"Status: {status}" in capsys.readouterr().out


def test_job_status_shows_task_details_and_logs_for_failure(project, capsys):
    _write_job(project, "j_failed", status="failed", created_at=10,
               exit_code=1, stderr="Traceback\nValueError: bad input\n",
               task={"description": "Run the failed task", "protocol": "proto.yml",
                     "model": "test-model", "mock": True})

    assert main(["job", "status", "j_failed"]) == 0
    output = capsys.readouterr().out
    assert "Error:" in output
    assert "ValueError: bad input" in output
    assert "Description: Run the failed task" in output
    assert "Model: test-model" in output
    assert "Mock: yes" in output


def test_job_show_and_logs_read_one_real_job(project, capsys):
    _write_job(project, "j_logs", status="completed", created_at=10,
               exit_code=0, stdout="first\nlast\n")

    assert main(["job", "logs", "j_logs", "--tail", "1"]) == 0
    assert capsys.readouterr().out == "last\n"
    assert main(["job", "status", "j_logs"]) == 0
    assert "Job: j_logs" in capsys.readouterr().out


def test_job_wait_returns_terminal_exit_code(project, capsys):
    _write_job(project, "j_wait", status="failed", created_at=10, exit_code=7,
               completed_at=12)

    assert main(["job", "wait", "j_wait"]) == 7
    output = capsys.readouterr().out
    assert "Job j_wait: failed" in output
    assert "Exit code: 7" in output


def test_job_cancel_updates_running_job(project, monkeypatch, capsys):
    job_dir = _write_job(project, "j_cancel", status="running", created_at=10)
    state = json.loads((job_dir / "state.json").read_text())
    state["pid"] = 12345
    (job_dir / "state.json").write_text(json.dumps(state))
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    assert main(["job", "cancel", "j_cancel"]) == 0
    assert "Job j_cancel cancelled." in capsys.readouterr().out
    assert json.loads((job_dir / "state.json").read_text())["status"] == "cancelled"


@pytest.mark.parametrize("job_setup", ["missing-state", "corrupt-state"])
def test_job_status_reports_missing_or_corrupt_job(project, capsys, job_setup):
    job_dir = project / ".snodo" / "jobs" / "j_broken"
    job_dir.mkdir()
    if job_setup == "corrupt-state":
        (job_dir / "state.json").write_text("not json")

    assert main(["job", "status", "j_broken"]) == 1
    error = capsys.readouterr().err
    assert "Error:" in error
    assert "j_broken" in error
