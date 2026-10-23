"""Job links on durable task run records."""

import json

from snodo.cli.commands.task_record import _record_task_completion, _record_task_start


def _read_state(tmp_path, task_id):
    return json.loads((tmp_path / ".snodo" / "tasks" / task_id / "state.json").read_text())


def test_task_start_records_job_id_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SNODO_JOB_ID", "j_run_123")

    _record_task_start(str(tmp_path), "task_alpha", "do the thing")

    assert _read_state(tmp_path, "task_alpha")["job_id"] == "j_run_123"


def test_task_start_omits_job_id_without_job(tmp_path, monkeypatch):
    monkeypatch.delenv("SNODO_JOB_ID", raising=False)

    _record_task_start(str(tmp_path), "task_alpha", "do the thing")

    assert "job_id" not in _read_state(tmp_path, "task_alpha")


def test_completion_preserves_job_id(tmp_path, monkeypatch):
    monkeypatch.setenv("SNODO_JOB_ID", "j_run_123")
    _record_task_start(str(tmp_path), "task_alpha", "do the thing")

    _record_task_completion(str(tmp_path), "task_alpha", "completed")

    state = _read_state(tmp_path, "task_alpha")
    assert state["job_id"] == "j_run_123"
    assert state["status"] == "completed"


def test_second_start_archives_job_id_of_previous_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("SNODO_JOB_ID", "j_first")
    _record_task_start(str(tmp_path), "task_alpha", "first run")
    monkeypatch.setenv("SNODO_JOB_ID", "j_second")

    _record_task_start(str(tmp_path), "task_alpha", "second run")

    attempts = list((tmp_path / ".snodo" / "tasks" / "task_alpha" / "attempts").glob("*.json"))
    assert len(attempts) == 1
    archived = json.loads(attempts[0].read_text())
    assert archived["job_id"] == "j_first"
    assert _read_state(tmp_path, "task_alpha")["job_id"] == "j_second"
