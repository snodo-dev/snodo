"""Tests for recording hand-completed tasks outside the loop.

FILE: tests/cli/test_task_complete.py
"""

import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from snodo.cli.commands.task_cmd import task_list_command, task_show_command
from snodo.cli.commands.task_complete import task_complete_command
from snodo.infrastructure.audit import AuditLog
from snodo.infrastructure.session import SessionManager
from snodo.infrastructure.state import ProjectState, write_state


def _setup_project(tmp_path, monkeypatch, mode="dev"):
    """Set up a test project root with .snodo directory and active session."""
    snodo_dir = tmp_path / ".snodo"
    snodo_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir = snodo_dir / "sessions"
    audit_log = AuditLog(str(snodo_dir / "audit.log"))
    mgr = SessionManager(audit_log=audit_log, sessions_dir=sessions_dir)
    session = mgr.create_session(mode, str(tmp_path))

    state = ProjectState(current_mode=mode, active_session={mode: session.session_id})
    write_state(str(tmp_path), state)

    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda p=None: audit_log)
    return mgr, session, audit_log


def test_hand_completion_record_written_and_distinguishable_from_engine(tmp_path, monkeypatch):
    """Audit record is written for hand completion and distinguishable from engine completion."""
    mgr, session, audit_log = _setup_project(tmp_path, monkeypatch)
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda p=None: audit_log)

    # 1. Simulate an engine completion (what loop._complete_node produces)
    audit_log.append_event("task_complete", {
        "op": "task_complete",
        "task_ref": "task_engine",
        "artifacts": ["snodo/models.py"],
    })

    # 2. Record a hand completion outside the loop
    args = SimpleNamespace(
        task_id="task_hand",
        plan=None,
        who="alice",
        notes="Merged manually after fixing compiler error",
        json=False,
    )
    res = task_complete_command(args)
    assert res == 0

    events = audit_log.get_history()
    engine_events = [e for e in events if (e.data or {}).get("task_ref") == "task_engine"]
    hand_events = [e for e in events if (e.data or {}).get("task_ref") == "task_hand"]

    assert len(engine_events) == 1
    assert len(hand_events) == 1

    engine_ev = engine_events[0]
    hand_ev = hand_events[0]

    # Verification of hand completion record
    assert hand_ev.event_type == "task_completed_by_hand"
    assert hand_ev.data["op"] == "task_completed_by_hand"
    assert hand_ev.data["who"] == "alice"
    assert "recorded_at" in hand_ev.data
    assert hand_ev.data["judged"] is False
    assert hand_ev.data["engine_judged"] is False
    assert hand_ev.data["outside_loop"] is True
    assert hand_ev.data["notes"] == "Merged manually after fixing compiler error"

    # Distinguishability predicate:
    # A reader inspecting the audit log can distinguish clean pass from hand-finished task.
    def is_clean_engine_pass(event) -> bool:
        data = event.data or {}
        op = data.get("op") or event.event_type
        return op == "task_complete" and data.get("judged") is not False and "who" not in data

    def is_hand_finished(event) -> bool:
        data = event.data or {}
        op = data.get("op") or event.event_type
        return op in ("task_completed_by_hand", "hand_completed") and data.get("judged") is False and "who" in data

    assert is_clean_engine_pass(engine_ev) is True
    assert is_hand_finished(engine_ev) is False

    assert is_clean_engine_pass(hand_ev) is False
    assert is_hand_finished(hand_ev) is True


def test_no_new_status_value_introduced(tmp_path, monkeypatch):
    """No new task status, halt type, or severity is introduced (respecting ADR 045)."""
    mgr, session, audit_log = _setup_project(tmp_path, monkeypatch)
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda p=None: audit_log)

    # Set up plan with status.json
    plan_dir = tmp_path / ".snodo" / "plans" / "demo_plan"
    plan_dir.mkdir(parents=True)
    plan_yml = {"name": "demo_plan", "intent": "test", "waves": [{"id": 1, "tasks": ["1.1_setup"]}]}
    (plan_dir / "plan.yml").write_text(yaml.dump(plan_yml))
    (plan_dir / "status.json").write_text(json.dumps({"tasks": {"1.1_setup": {"status": "blocked"}}}))

    # Complete the task by hand
    args = SimpleNamespace(task_id="1.1_setup", plan="demo_plan", who="dev_user", notes=None, json=False)
    res = task_complete_command(args)
    assert res == 0

    # Read status.json
    status_data = json.loads((plan_dir / "status.json").read_text())
    entry = status_data["tasks"]["1.1_setup"]

    # Assert status value is strictly the existing "completed"
    assert entry["status"] == "completed"

    # Read closed vocabularies baseline
    baseline_path = Path("scripts") / "vocabularies_baseline.txt"
    assert baseline_path.exists()
    baseline_lines = baseline_path.read_text().splitlines()
    baselined_statuses = {
        line.split()[1] for line in baseline_lines if line.strip() and line.startswith("task_status ")
    }

    assert entry["status"] in baselined_statuses
    assert "hand_completed" not in baselined_statuses
    assert "completed_by_hand" not in baselined_statuses

    # Run the vocabulary enforcement script
    from scripts.enforce_vocabularies import main as vocab_main
    assert vocab_main([]) == 0


def test_plan_advances_from_hand_completion_record(tmp_path, monkeypatch):
    """The plan advances from the hand-completion record without manual status.json edits."""
    mgr, session, audit_log = _setup_project(tmp_path, monkeypatch)
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda p=None: audit_log)

    from snodo.cli.commands.plan_run import _get_completed_waves, _task_completed

    # Two wave plan: wave 2 depends on wave 1
    plan_dir = tmp_path / ".snodo" / "plans" / "two_wave_plan"
    plan_dir.mkdir(parents=True)
    plan_yml = {
        "name": "two_wave_plan",
        "intent": "advance test",
        "waves": [
            {"id": 1, "depends_on": [], "tasks": ["1.1_task"]},
            {"id": 2, "depends_on": [1], "tasks": ["2.1_task"]},
        ],
    }
    (plan_dir / "plan.yml").write_text(yaml.dump(plan_yml))
    # 1.1_task is currently blocked
    (plan_dir / "status.json").write_text(json.dumps({
        "tasks": {
            "1.1_task": {"status": "blocked"},
            "2.1_task": {"status": "pending"},
        }
    }))

    # Before completion: wave 1 is not complete
    initial_status = json.loads((plan_dir / "status.json").read_text())["tasks"]
    assert not _task_completed(initial_status, "1.1_task")
    assert 1 not in _get_completed_waves(plan_yml["waves"], initial_status)

    # Operator records hand completion (auto-discovering plan)
    args = SimpleNamespace(task_id="1.1_task", plan=None, who="bob", notes="Fixed by hand", json=False)
    res = task_complete_command(args)
    assert res == 0

    # After completion: plan status file was updated by the command
    updated_status = json.loads((plan_dir / "status.json").read_text())["tasks"]
    assert _task_completed(updated_status, "1.1_task") is True
    assert updated_status["1.1_task"]["completed_by"] == "bob"
    assert updated_status["1.1_task"]["judged"] is False

    # Wave 1 is now marked completed, allowing wave 2 to advance
    completed_waves = _get_completed_waves(plan_yml["waves"], updated_status)
    assert 1 in completed_waves or "1" in completed_waves


def test_task_complete_cli_json_and_show(tmp_path, monkeypatch, capsys):
    """task complete --json outputs valid schema, and task show surfaces hand completion."""
    mgr, session, audit_log = _setup_project(tmp_path, monkeypatch)
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda p=None: audit_log)
    monkeypatch.setattr("snodo.infrastructure.session.SessionManager", lambda *a, **kw: mgr)

    # 1. Complete with --json
    args = SimpleNamespace(
        task_id="task_json_test",
        plan=None,
        who="charlie",
        notes="CLI json test",
        json=True,
    )
    res = task_complete_command(args)
    assert res == 0

    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["ok"] is True
    assert data["schema"] == "snodo.task_complete.v1"
    assert data["task_id"] == "task_json_test"
    assert data["who"] == "charlie"
    assert data["judged"] is False
    assert data["notes"] == "CLI json test"

    # 2. snodo task show --json
    show_args = SimpleNamespace(task_id="task_json_test", json=True)
    res = task_show_command(show_args)
    assert res == 0
    show_data = json.loads(capsys.readouterr().out)
    assert show_data["ok"] is True
    assert show_data["status"] == "completed"
    assert show_data["hand_completion"]["who"] == "charlie"
    assert show_data["hand_completion"]["judged"] is False

    # 3. snodo task show (text)
    show_args_text = SimpleNamespace(task_id="task_json_test", json=False)
    res = task_show_command(show_args_text)
    assert res == 0
    text_out = capsys.readouterr().out
    assert "Completed outside loop (by hand):" in text_out
    assert "who:         charlie" in text_out
    assert "judged:      False (completed outside engine loop)" in text_out


def test_task_complete_clears_failure_context(tmp_path, monkeypatch):
    """task complete clears failure context from active session."""
    mgr, session, audit_log = _setup_project(tmp_path, monkeypatch)
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda p=None: audit_log)
    monkeypatch.setattr("snodo.infrastructure.session.SessionManager", lambda *a, **kw: mgr)

    # Seed failure context
    mgr.update_decision(session.session_id, "task_failure", {"task_fail": {"attempt": 2, "spec": "test"}})
    s = mgr.get_active_session("dev", str(tmp_path))
    assert "task_fail" in s.checkpoint.decisions["task_failure"]

    args = SimpleNamespace(task_id="task_fail", plan=None, who="operator", notes=None, json=False)
    res = task_complete_command(args)
    assert res == 0

    s2 = mgr.get_active_session("dev", str(tmp_path))
    assert "task_fail" not in s2.checkpoint.decisions.get("task_failure", {})


def test_task_list_reflects_hand_completion(tmp_path, monkeypatch, capsys):
    """task list reflects status as completed for hand-completed tasks."""
    mgr, session, audit_log = _setup_project(tmp_path, monkeypatch)
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("snodo.infrastructure.audit.get_audit_log", lambda p=None: audit_log)
    monkeypatch.setattr("snodo.infrastructure.session.SessionManager", lambda *a, **kw: mgr)

    # Complete a task by hand
    args = SimpleNamespace(task_id="task_list_check", plan=None, who="operator", notes=None, json=False)
    task_complete_command(args)

    res = task_list_command(SimpleNamespace())
    assert res == 0
    out = capsys.readouterr().out
    assert "task_list_check" in out
    assert "completed" in out
