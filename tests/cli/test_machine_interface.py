"""Tests for the versioned machine interface (ADR 022).

Every ``--json`` command must:
- emit a single JSON object to stdout that parses,
- carry a ``schema`` field of the form ``snodo.<command>.v1``,
- use stable field names (asserted here, so a rename fails the suite).

``snodo validate`` must additionally return exit codes that distinguish the
four outcomes.
"""

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from snodo.cli.json_output import (
    EXIT_BLOCKER,
    EXIT_ESCALATE,
    EXIT_INTERNAL_ERROR,
    EXIT_PASS,
    EXIT_VALIDATOR_ERROR,
    OUTCOME_EXIT_CODES,
    SCHEMA_VERSION,
    schema_name,
)


def _parse_stdout(capsys):
    """Parse the single JSON object written to stdout."""
    out = capsys.readouterr().out
    return json.loads(out)


# ---------------------------------------------------------------------------
# Schema + exit-code map
# ---------------------------------------------------------------------------

class TestSchema:
    def test_schema_name_is_versioned(self):
        assert schema_name("status") == f"snodo.status.v{SCHEMA_VERSION}"

    def test_exit_codes_distinguish_four_outcomes(self):
        assert OUTCOME_EXIT_CODES == {
            "pass": 0,
            "blocker": 1,
            "escalate": 2,
            "validator_error": 3,
            "internal_error": 4,
        }
        assert len(set(OUTCOME_EXIT_CODES.values())) == 5


# ---------------------------------------------------------------------------
# status --json
# ---------------------------------------------------------------------------

class TestStatusJson:
    def test_status_json_parses_and_carries_schema(self, tmp_path, capsys):
        from snodo.infrastructure.session import SessionManager
        from snodo.infrastructure.state import ProjectState, write_state

        from snodo.cli.commands.status_cmd import status_command

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
                result = status_command(SimpleNamespace(json=True))

        assert result == 0
        data = _parse_stdout(capsys)
        assert data["schema"] == "snodo.status.v1"
        assert data["ok"] is True
        # Stable field names.
        assert set(data.keys()) == {
            "schema", "ok", "project_root", "protocol", "mode",
            "active_session", "last_run",
        }
        assert data["protocol"]["id"] == "test"
        assert data["protocol"]["name"] == "Test Protocol"
        assert data["mode"] == "producer"
        assert data["active_session"] == session.session_id
        assert data["last_run"]["session_id"] == session.session_id


# ---------------------------------------------------------------------------
# mode show --json
# ---------------------------------------------------------------------------

class TestModeJson:
    def test_mode_show_json(self, tmp_path, capsys):
        from snodo.infrastructure.state import ProjectState, write_state

        from snodo.cli.commands.mode_cmd import mode_command

        snodo_dir = tmp_path / ".snodo"
        snodo_dir.mkdir()
        (snodo_dir / "protocol.yml").write_text(
            "protocol_id: \"test\"\nname: \"Test\"\nversion: \"1.0.0\"\n"
            "modes:\n  - mode_id: \"producer\"\n    name: \"Producer\"\n"
            "    tools: [\"edit\"]\n    validators: []\n    transitions: {}\n"
            "validators: []\n"
            "disagreement_policy: \"unanimous\"\ninitial_mode: \"producer\"\n"
        )
        write_state(str(tmp_path), ProjectState(current_mode="producer"))

        with patch("snodo.infrastructure.paths.require_project_root", return_value=str(tmp_path)):
            result = mode_command(SimpleNamespace(mode_action="show", json=True))

        assert result == 0
        data = _parse_stdout(capsys)
        assert data["schema"] == "snodo.mode.v1"
        assert set(data.keys()) == {"schema", "ok", "mode", "name", "active_session"}
        assert data["mode"] == "producer"


# ---------------------------------------------------------------------------
# session show --json
# ---------------------------------------------------------------------------

class TestSessionJson:
    def test_session_show_json(self, tmp_path, capsys):
        from snodo.infrastructure.session import SessionManager

        from snodo.cli.commands.session_cmd import session_command

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        mgr = SessionManager(sessions_dir=sessions_dir)
        session = mgr.create_session("producer", str(tmp_path))

        args = SimpleNamespace(
            session_action="show", session_id=session.session_id, json=True,
            sessions_dir=sessions_dir,
        )
        result = session_command(args)

        assert result == 0
        data = _parse_stdout(capsys)
        assert data["schema"] == "snodo.session.v1"
        assert set(data.keys()) == {
            "schema", "ok", "session_id", "mode", "project_root", "project_id",
            "created_at", "updated_at", "checkpoint",
        }
        assert data["session_id"] == session.session_id
        assert data["mode"] == "producer"
        assert set(data["checkpoint"].keys()) == {
            "current_task", "decisions", "memory_summary",
        }

    def test_session_show_json_missing(self, tmp_path, capsys):
        from snodo.infrastructure.session import SessionManager

        from snodo.cli.commands.session_cmd import session_command

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        SessionManager(sessions_dir=sessions_dir)

        args = SimpleNamespace(
            session_action="show", session_id="sess_nope", json=True,
            sessions_dir=sessions_dir,
        )
        result = session_command(args)

        assert result == 1
        data = _parse_stdout(capsys)
        assert data["schema"] == "snodo.session.v1"
        assert data["ok"] is False
        assert "error" in data


# ---------------------------------------------------------------------------
# task show --json
# ---------------------------------------------------------------------------

class TestTaskJson:
    def test_task_show_json(self, tmp_path, capsys):
        from snodo.infrastructure.session import SessionManager
        from snodo.infrastructure.state import ProjectState, write_state

        from snodo.cli.commands.task_cmd import task_show_command

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

        with patch("snodo.cli.commands.task_cmd.resolve_project_root", return_value=str(tmp_path)):
            with patch("snodo.infrastructure.session.SessionManager", return_value=mgr):
                result = task_show_command(SimpleNamespace(task_id="task_abc", json=True))

        assert result == 0
        data = _parse_stdout(capsys)
        assert data["schema"] == "snodo.task.v1"
        assert set(data.keys()) == {
            "schema", "ok", "task_id", "session_id", "mode", "halt", "failure", "spec",
        }
        assert data["task_id"] == "task_abc"
        assert data["halt"]["final_decision"] == "escalate"


# ---------------------------------------------------------------------------
# worktree list --json
# ---------------------------------------------------------------------------

class TestWorktreeJson:
    def test_worktree_list_json(self, tmp_path, capsys):
        from snodo.infrastructure.worktree import create_worktree

        from snodo.cli.commands.worktree_cmd import worktree_list_command

        root = tmp_path / "proj"
        root.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
        (root / "README.md").write_text("init\n")
        subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)

        create_worktree(str(root), "task_a", "spec a")

        with patch("snodo.cli.commands.worktree_cmd.require_project_root", return_value=str(root)):
            result = worktree_list_command(SimpleNamespace(json=True))

        assert result == 0
        data = _parse_stdout(capsys)
        assert data["schema"] == "snodo.worktree.v1"
        assert set(data.keys()) == {"schema", "ok", "project_root", "worktrees"}
        assert len(data["worktrees"]) == 1
        assert data["worktrees"][0]["task_id"] == "task_a"
        assert set(data["worktrees"][0].keys()) == {"task_id", "path", "age_days"}


# ---------------------------------------------------------------------------
# validate — four outcomes + exit codes
# ---------------------------------------------------------------------------

def _validate_protocol_data():
    return {
        "protocol_id": "contract",
        "name": "Contract",
        "version": "1.0.0",
        "modes": [
            {
                "mode_id": "producer",
                "name": "Producer",
                "tools": ["edit", "dispatch"],
                "validators": ["security"],
            },
        ],
        "validators": [
            {
                "validator_id": "security",
                "validator_type": "security",
                "criteria": ["Check security"],
            },
        ],
        "disagreement_policy": "unanimous",
        "initial_mode": "producer",
    }


def _completion_fn(severity):
    msg = MagicMock()
    msg.content = json.dumps({"severity": severity, "justification": "ok"})
    response = MagicMock()
    response.choices = [MagicMock(message=msg)]
    return MagicMock(return_value=response)


def _mock_validator_config():
    return MagicMock(max_tokens=1500, max_tool_turns=6)


@pytest.fixture
def validate_project(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=False)
    subprocess.run(["git", "config", "user.email", "t@t.co"], cwd=tmp_path, check=False)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=False)
    (tmp_path / "README.md").write_text("test")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=False)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=False)
    snodo_dir = tmp_path / ".snodo"
    snodo_dir.mkdir(exist_ok=True)
    (snodo_dir / "protocol.yml").write_text(
        "protocol_id: \"contract\"\nname: \"Contract\"\nversion: \"1.0.0\"\n"
        "modes:\n  - mode_id: \"producer\"\n    name: \"Producer\"\n"
        "    tools: [\"edit\", \"dispatch\"]\n    validators: [\"security\"]\n"
        "validators:\n  - validator_id: \"security\"\n"
        "    validator_type: \"security\"\n    criteria: [\"Check security\"]\n"
        "disagreement_policy: \"unanimous\"\ninitial_mode: \"producer\"\n"
    )
    (snodo_dir / "state.json").write_text(
        json.dumps({"current_mode": "producer", "active_session": {}})
    )
    return tmp_path


def _run_validate(project_root, severity, capsys):
    from snodo.cli.commands.validate_cmd import validate_command

    with patch(
        "snodo.validators.runner.resolve_validator_completion",
        return_value=(_completion_fn(severity), "mock-model", _mock_validator_config()),
    ), patch(
        "snodo.validators.llm_validator.supports_response_schema", return_value=False
    ), patch(
        "snodo.infrastructure.paths.resolve_project_root",
        return_value=str(project_root),
    ):
        return validate_command(SimpleNamespace(
            task_spec="do the thing", phase="pre_execute",
            protocol=".snodo/protocol.yml", mode=None, json=True,
        ))


class TestValidateJson:
    def test_pass_exit_zero(self, validate_project, capsys):
        result = _run_validate(validate_project, "pass", capsys)
        assert result == EXIT_PASS
        data = _parse_stdout(capsys)
        assert data["schema"] == "snodo.validate.v1"
        assert data["status"] == "pass"
        assert set(data.keys()) == {
            "schema", "ok", "status", "task_id", "phase", "mode",
            "results", "policy_decision", "instruction",
        }
        assert data["results"][0]["validator_id"] == "security"
        assert set(data["results"][0].keys()) == {
            "validator_id", "severity", "justification",
        }

    def test_blocker_exit_one(self, validate_project, capsys):
        result = _run_validate(validate_project, "blocker", capsys)
        assert result == EXIT_BLOCKER
        data = _parse_stdout(capsys)
        assert data["status"] == "blocker"

    def test_escalate_exit_two(self, validate_project, capsys):
        result = _run_validate(validate_project, "warn", capsys)
        assert result == EXIT_ESCALATE
        data = _parse_stdout(capsys)
        assert data["status"] == "escalate"

    def test_validator_error_exit_three(self, validate_project, capsys):
        from snodo.cli.commands.validate_cmd import validate_command

        with patch(
            "snodo.validators.runner.resolve_validator_completion",
            side_effect=RuntimeError("config broken"),
        ), patch(
            "snodo.infrastructure.paths.resolve_project_root",
            return_value=str(validate_project),
        ):
            result = validate_command(SimpleNamespace(
                task_spec="do the thing", phase="pre_execute",
                protocol=".snodo/protocol.yml", mode=None, json=True,
            ))

        assert result == EXIT_VALIDATOR_ERROR
        data = _parse_stdout(capsys)
        assert data["status"] == "validator_error"

    def test_not_in_project_internal_error(self, tmp_path, capsys):
        from snodo.cli.commands.validate_cmd import validate_command

        with patch(
            "snodo.infrastructure.paths.resolve_project_root",
            return_value=None,
        ):
            result = validate_command(SimpleNamespace(
                task_spec="do the thing", phase="pre_execute",
                protocol=".snodo/protocol.yml", mode=None, json=True,
            ))

        assert result == EXIT_INTERNAL_ERROR
        data = _parse_stdout(capsys)
        assert data["ok"] is False
        assert "error" in data
