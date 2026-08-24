"""Tests for observability commands and inspect-command suggestions.

FILE: tests/cli/test_followup_commands.py

Asserts that every id the CLI surfaces is accompanied by a command that
inspects it, and that each suggested command resolves to a real CLI command
(a suggestion that 404s is worse than none).
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import typer

from snodo.cli.main import app


def _click_app():
    """Return the resolved click Group for the snodo CLI."""
    return typer.main.get_command(app)


def _resolve_command(tokens):
    """Resolve a command path (subcommand names only) against the CLI.

    Raises KeyError if any segment does not exist (i.e. the suggestion 404s).
    """
    group = _click_app()
    for token in tokens:
        group = group.commands[token]
    return group


# === Command resolution (no 404s) ===

class TestSuggestedCommandsResolve:
    def test_all_suggested_commands_exist(self):
        """Every command the CLI can suggest must resolve to a real command."""
        command_paths = [
            ["session", "show"],
            ["session", "list"],
            ["task", "show"],
            ["task", "list"],
            ["task", "abandon"],
            ["job", "status"],
            ["job", "logs"],
            ["job", "wait"],
            ["meta"],
            ["logs"],
            ["run"],
            ["mode", "show"],
            ["status"],
        ]
        for tokens in command_paths:
            _resolve_command(tokens)  # raises KeyError on 404


# === snodo status ===

class TestStatusCommand:
    def test_status_shows_protocol_mode_session(self, tmp_path, capsys):
        from snodo.cli.commands.status_cmd import status_command
        from snodo.infrastructure.session import SessionManager
        from snodo.infrastructure.state import write_state, ProjectState

        snodo_dir = tmp_path / ".snodo"
        snodo_dir.mkdir()
        (snodo_dir / "protocol.yml").write_text(
            "protocol_id: \"test\"\nname: \"Test Protocol\"\n"
        )
        write_state(str(tmp_path), ProjectState(current_mode="producer"))

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        mgr = SessionManager(sessions_dir=sessions_dir)
        session = mgr.create_session("producer", str(tmp_path))

        with patch("snodo.infrastructure.paths.require_project_root", return_value=str(tmp_path)):
            with patch("snodo.infrastructure.session.SessionManager", return_value=mgr):
                result = status_command(SimpleNamespace())

        assert result == 0
        out = capsys.readouterr().out
        assert "Protocol: Test Protocol (test)" in out
        assert "Mode:     producer" in out
        assert f"Session:  {session.session_id}" in out
        assert "Last run:" in out

    def test_status_no_sessions(self, tmp_path, capsys):
        from snodo.cli.commands.status_cmd import status_command
        from snodo.infrastructure.session import SessionManager
        from snodo.infrastructure.state import write_state, ProjectState

        snodo_dir = tmp_path / ".snodo"
        snodo_dir.mkdir()
        (snodo_dir / "protocol.yml").write_text("protocol_id: \"test\"\n")
        write_state(str(tmp_path), ProjectState(current_mode="producer"))

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        mgr = SessionManager(sessions_dir=sessions_dir)

        with patch("snodo.infrastructure.paths.require_project_root", return_value=str(tmp_path)):
            with patch("snodo.infrastructure.session.SessionManager", return_value=mgr):
                result = status_command(SimpleNamespace())

        assert result == 0
        out = capsys.readouterr().out
        assert "Last run: (none)" in out


# === snodo task show ===

class TestTaskShowCommand:
    def test_task_show_halt_and_failure(self, tmp_path, capsys):
        from snodo.cli.commands.task_cmd import task_show_command
        from snodo.infrastructure.session import SessionManager
        from snodo.infrastructure.state import write_state, ProjectState

        snodo_dir = tmp_path / ".snodo"
        snodo_dir.mkdir()
        write_state(str(tmp_path), ProjectState(current_mode="producer"))

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        mgr = SessionManager(sessions_dir=sessions_dir)
        session = mgr.create_session("producer", str(tmp_path))
        mgr.update_decision(session.session_id, "halt", {
            "task_abc": {"final_decision": "escalate", "halt_type": "escalated",
                         "phase": "pre_execute", "validator_results": []},
        })
        mgr.update_decision(session.session_id, "task_failure", {
            "task_abc": {"attempt": 1, "branch": "task/task_abc", "files_changed": []},
        })

        with patch("snodo.cli.commands.task_cmd.resolve_project_root", return_value=str(tmp_path)):
            with patch("snodo.infrastructure.session.SessionManager", return_value=mgr):
                result = task_show_command(SimpleNamespace(task_id="task_abc"))

        assert result == 0
        out = capsys.readouterr().out
        assert "Task:    task_abc" in out
        assert "final_decision: escalate" in out
        assert "attempt: 1" in out
        assert "snodo session show" in out
        assert "snodo run --retry task_abc" in out

    def test_task_show_unknown_task(self, tmp_path, capsys):
        from snodo.cli.commands.task_cmd import task_show_command
        from snodo.infrastructure.session import SessionManager
        from snodo.infrastructure.state import write_state, ProjectState

        snodo_dir = tmp_path / ".snodo"
        snodo_dir.mkdir()
        write_state(str(tmp_path), ProjectState(current_mode="producer"))

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        mgr = SessionManager(sessions_dir=sessions_dir)
        mgr.create_session("producer", str(tmp_path))

        with patch("snodo.cli.commands.task_cmd.resolve_project_root", return_value=str(tmp_path)):
            with patch("snodo.infrastructure.session.SessionManager", return_value=mgr):
                result = task_show_command(SimpleNamespace(task_id="task_nope"))

        assert result == 1
        assert "No record for task" in capsys.readouterr().out


# === session list sorted by recency + per-row inspect ===

class TestSessionListRecency:
    def test_sorted_by_recency_with_inspect(self, tmp_path, capsys):
        from snodo.cli.commands.session_cmd import session_command
        from snodo.infrastructure.session import SessionManager

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        mgr = SessionManager(sessions_dir=sessions_dir)
        older = mgr.create_session("producer", "/tmp/proj")
        newer = mgr.create_session("producer", "/tmp/proj")

        # Backdate the older session so it sorts last.
        path = sessions_dir / f"{older.session_id}.json"
        data = json.loads(path.read_text())
        data["updated_at"] = "2000-01-01T00:00:00+00:00"
        path.write_text(json.dumps(data, indent=2))

        args = SimpleNamespace(
            session_action="list", mode=None, project=None,
            sessions_dir=sessions_dir,
        )
        result = session_command(args)

        assert result == 0
        out = capsys.readouterr().out
        assert out.index(newer.session_id) < out.index(older.session_id)
        assert f"inspect: snodo session show {newer.session_id}" in out


# === run header + halt footer suggestions ===

class TestRunHeaderSuggestions:
    def test_execute_task_prints_task_inspect(self, tmp_path, capsys):
        from snodo.cli.commands.run_cmd import _execute_task
        from snodo.core.interfaces import Task

        protocol = SimpleNamespace(
            name="test", initial_mode="producer",
            execution=SimpleNamespace(max_total_fix_attempts=10, max_recovery_depth=3),
        )
        task = Task(id="task_abc", spec="do stuff")
        args = SimpleNamespace(
            mock=True, verbose=False, audit_log=None, session_manager=None,
            resume=None,
        )

        with patch("snodo.infrastructure.paths.require_project_root", return_value=str(tmp_path)):
            with patch("snodo.cli.commands.run_cmd._resolve_session", return_value=(None, "producer")):
                with patch("snodo.cli.commands.run_cmd._setup_memory", return_value=(None, None, None)):
                    with patch("snodo.infrastructure.worktree.setup_for_task", return_value=None):
                        with patch("snodo.cli.commands.run_cmd._build_graph", return_value=None):
                            result = _execute_task(args, protocol, task, "gpt-4")

        assert result == 1
        out = capsys.readouterr().out
        assert "Task ID: task_abc" in out
        assert "Inspect: snodo task show task_abc" in out

    def test_resolve_session_prints_inspect(self, tmp_path, capsys):
        from snodo.cli.commands.run_cmd import _resolve_session
        from snodo.infrastructure.session import SessionManager
        from snodo.infrastructure.state import ProjectState

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        mgr = SessionManager(sessions_dir=sessions_dir)
        args = SimpleNamespace(resume=None)
        protocol = SimpleNamespace(initial_mode="producer")

        with patch("snodo.infrastructure.state.read_state",
                   return_value=ProjectState(current_mode="producer")):
            session, mode = _resolve_session(args, mgr, protocol, str(tmp_path))

        out = capsys.readouterr().out
        assert f"Inspect: snodo session show {session.session_id}" in out


class TestHaltFooterSuggestions:
    def test_halt_footer_prints_followup(self, capsys):
        from snodo.cli.commands.run_cmd import _report_closure
        from snodo.engine.closure import ClosureNode

        payload = {
            "halt_type": "escalate",
            "final_decision": "escalate",
            "status": "blocked",
            "reason": "test",
            "task_id": "task_abc",
            "task_spec": "do stuff",
            "validator_results": [
                {"validator_id": "sec", "severity": "warn", "justification": "x"},
            ],
        }
        tree = ClosureNode(task_id="task_abc", depth=0, outcome="escalate",
                           halt_payload=payload)
        result = _report_closure(tree, {}, session_id="sess_xyz")

        assert result == 1
        out = capsys.readouterr().out
        assert "Follow-up:" in out
        assert "snodo session show sess_xyz" in out
        assert "snodo task show task_abc" in out
        assert 'snodo run --retry task_abc "revised spec"' in out

    def test_halt_footer_no_retry_on_completed(self, capsys):
        from snodo.cli.commands.run_cmd import _report_closure
        from snodo.engine.closure import ClosureNode

        payload = {
            "halt_type": "completed",
            "final_decision": "completed",
            "status": "completed",
            "task_id": "task_abc",
            "task_spec": "do stuff",
            "validator_results": [],
        }
        tree = ClosureNode(task_id="task_abc", depth=0, outcome="resolved",
                           halt_payload=payload)
        _report_closure(tree, {"is_blocked": False, "is_complete": True, "artifacts": []},
                        session_id="sess_xyz")
        out = capsys.readouterr().out
        assert "snodo run --retry" not in out


# === works-when-pasted (integration) ===

class TestWorksWhenPasted:
    def test_session_show_and_task_show_work(self, tmp_path, monkeypatch):
        from snodo.infrastructure.session import SessionManager
        from snodo.infrastructure.state import write_state, ProjectState
        from snodo.cli.main import main

        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("SNODO_HOME", str(home))

        snodo_dir = tmp_path / ".snodo"
        snodo_dir.mkdir()
        write_state(str(tmp_path), ProjectState(current_mode="producer"))

        mgr = SessionManager()  # uses SNODO_HOME/sessions
        session = mgr.create_session("producer", str(tmp_path))
        mgr.update_decision(session.session_id, "halt", {
            "task_abc": {"final_decision": "escalate", "halt_type": "escalated",
                         "phase": "pre_execute", "validator_results": []},
        })

        monkeypatch.chdir(tmp_path)

        assert main(["session", "show", session.session_id]) == 0
        assert main(["task", "show", "task_abc"]) == 0

    def test_job_commands_work(self, tmp_path, monkeypatch):
        from snodo.cli.main import main

        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("SNODO_HOME", str(home))

        snodo_dir = tmp_path / ".snodo"
        snodo_dir.mkdir()
        job_dir = snodo_dir / "jobs" / "j_abc123"
        job_dir.mkdir(parents=True)
        (job_dir / "task.json").write_text(json.dumps({
            "description": "do stuff", "task_id": "task_abc",
        }))
        (job_dir / "state.json").write_text(json.dumps({
            "status": "completed", "pid": None, "created_at": 1700000000,
            "started_at": 1700000001, "completed_at": 1700000010, "exit_code": 0,
        }))
        (job_dir / "stdout.log").write_text("hello\n")

        monkeypatch.chdir(tmp_path)

        assert main(["job", "status", "j_abc123"]) == 0
        assert main(["job", "logs", "j_abc123"]) == 0
        assert main(["job", "wait", "j_abc123"]) == 0
        assert main(["meta", "j_abc123"]) == 0

    def test_retry_command_works(self, tmp_path, monkeypatch):
        from snodo.infrastructure.session import SessionManager
        from snodo.infrastructure.state import write_state, ProjectState
        from snodo.cli.main import main

        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("SNODO_HOME", str(home))

        snodo_dir = tmp_path / ".snodo"
        snodo_dir.mkdir()
        (snodo_dir / "protocol.yml").write_text(
            "protocol_id: \"test\"\nname: \"Test\"\nversion: \"1.0.0\"\n"
            "modes:\n  - mode_id: \"producer\"\n    name: \"Producer\"\n"
            "    tools: [\"edit\"]\n    validators: [\"security\"]\n"
            "    transitions: {}\n"
            "validators:\n  - validator_id: \"security\"\n"
            "    validator_type: \"security\"\n"
            "    evaluation_phase: \"pre_execute\"\n    criteria: [\"check\"]\n"
            "disagreement_policy: \"unanimous\"\ninitial_mode: \"producer\"\n"
            "global_constraints: []\n"
        )
        write_state(str(tmp_path), ProjectState(current_mode="producer"))

        mgr = SessionManager()  # uses SNODO_HOME/sessions
        session = mgr.create_session("producer", str(tmp_path))
        mgr.update_decision(session.session_id, "task_failure", {
            "task_abc": {"attempt": 1, "spec": "do stuff", "branch": "task/task_abc",
                         "failed_validators": [], "files_changed": []},
        })

        monkeypatch.chdir(tmp_path)

        with patch("snodo.cli.commands.run_cmd._execute_task", return_value=0):
            assert main(["run", "--retry", "task_abc", "revised spec"]) == 0
