"""Retry falls back to the halt record when task_failure context is absent (Fixes #121).

FILE: tests/cli/test_retry_halt_fallback.py
"""

from types import SimpleNamespace

import pytest
from snodo.infrastructure.session import SessionManager
from snodo.infrastructure.state import ProjectState, write_state
from snodo.protocols import _TEMPLATE_PROTOCOLS

from snodo.cli.commands.run_cmd import _retry_task


def _setup(tmp_path, monkeypatch):
    project_root = str(tmp_path)
    protocol = _TEMPLATE_PROTOCOLS["solo"]
    mode = protocol.modes[0].mode_id
    write_state(project_root, ProjectState(current_mode=mode))

    session_mgr = SessionManager(sessions_dir=tmp_path / ".snodo" / "sessions")
    session = session_mgr.create_session(mode, project_root)

    monkeypatch.setattr(
        "snodo.cli.commands.run_cmd.load_protocol", lambda path: protocol
    )
    executed = []

    def mock_execute_task(args, prot, t, m):
        executed.append((t.id, t.spec))
        return 0

    monkeypatch.setattr("snodo.cli.commands.run_cmd._execute_task", mock_execute_task)

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml",
        model="mock-model",
        description=None,
    )
    return project_root, session_mgr, session, args, executed


def _halt_record(task_id="task_halt1", spec="implement the thing", **overrides):
    record = {
        "status": "blocked",
        "halt_type": "escalate",
        "final_decision": "escalate",
        "raw_halt_type": "escalate",
        "reason": None,
        "task_id": task_id,
        "task_spec": spec,
        "phase": "post_execute",
        "validator_results": [
            {
                "validator_id": "quality",
                "severity": "blocker",
                "justification": "tests do not pass",
            },
            {
                "validator_id": "coverage",
                "severity": "pass",
                "justification": "coverage fine",
            },
        ],
        "hint": "resolve the disagreement",
    }
    record.update(overrides)
    return record


def test_retry_from_halt_record_only(tmp_path, monkeypatch, capsys):
    """A task with only a halt record (no task_failure) retries successfully."""
    project_root, session_mgr, session, args, executed = _setup(tmp_path, monkeypatch)
    session_mgr.update_decision(
        session.session_id, "halt", {"task_halt1": _halt_record()}
    )

    res = _retry_task(args, "task_halt1", project_root, session_mgr)

    assert res == 0
    assert len(executed) == 1
    task_id, spec = executed[0]
    assert task_id == "task_halt1"
    assert "Original spec: implement the thing" in spec
    assert "quality: tests do not pass" in spec
    assert "coverage fine" not in spec
    assert "(attempt 2/" in capsys.readouterr().out


def test_retry_from_halt_record_without_validator_results(tmp_path, monkeypatch):
    """failed_validators is synthesised from reason when validator_results are empty."""
    project_root, session_mgr, session, args, executed = _setup(tmp_path, monkeypatch)
    record = _halt_record(validator_results=[], reason="coder crashed: head not moved")
    session_mgr.update_decision(session.session_id, "halt", {"task_halt1": record})

    res = _retry_task(args, "task_halt1", project_root, session_mgr)

    assert res == 0
    _, spec = executed[0]
    assert "escalate: coder crashed: head not moved" in spec


def test_task_failure_preferred_over_halt_record(tmp_path, monkeypatch):
    """When both records exist, task_failure is the source, not the halt record."""
    project_root, session_mgr, session, args, executed = _setup(tmp_path, monkeypatch)
    session_mgr.update_decision(
        session.session_id, "halt", {"task_halt1": _halt_record()}
    )
    session_mgr.update_decision(session.session_id, "task_failure", {
        "task_halt1": {
            "spec": "the authoritative spec",
            "branch": "task/task_halt1/authoritative",
            "attempt": 1,
            "failed_validators": [
                {
                    "validator_id": "tf-validator",
                    "severity": "blocker",
                    "justification": "failure-context reason",
                }
            ],
            "files_changed": ["src/a.py"],
        },
    })

    res = _retry_task(args, "task_halt1", project_root, session_mgr)

    assert res == 0
    _, spec = executed[0]
    assert "Original spec: the authoritative spec" in spec
    assert "tf-validator: failure-context reason" in spec
    assert "src/a.py" in spec
    assert "implement the thing" not in spec
    assert "tests do not pass" not in spec


def test_retry_without_any_record_still_fails(tmp_path, monkeypatch, capsys):
    """A task with neither record still prints the existing error and returns 1."""
    project_root, session_mgr, session, args, executed = _setup(tmp_path, monkeypatch)
    session_mgr.update_decision(session.session_id, "halt", {"other_task": _halt_record()})

    res = _retry_task(args, "task_halt1", project_root, session_mgr)

    assert res == 1
    assert len(executed) == 0
    assert "No failure context for task_halt1. Cannot retry." in capsys.readouterr().err


@pytest.mark.parametrize(
    "bad_record",
    [
        _halt_record(task_id="different_task"),
        _halt_record(status="completed", final_decision="completed"),
    ],
    ids=["task_id-mismatch", "not-a-blocked-halt"],
)
def test_retry_rejects_non_matching_halt_records(tmp_path, monkeypatch, capsys, bad_record):
    """Only a task_id-matching blocked halt may seed the fallback."""
    project_root, session_mgr, session, args, executed = _setup(tmp_path, monkeypatch)
    session_mgr.update_decision(session.session_id, "halt", {"task_halt1": bad_record})

    res = _retry_task(args, "task_halt1", project_root, session_mgr)

    assert res == 1
    assert len(executed) == 0
    assert "No failure context for task_halt1. Cannot retry." in capsys.readouterr().err
