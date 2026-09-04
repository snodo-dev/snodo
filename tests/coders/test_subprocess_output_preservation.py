"""Tests for preserving coder closing output and distinct stream truncation.

FILE: tests/coders/test_subprocess_output_preservation.py (Fixes #212)

PROVES:
- When a coder completes (exit 0) having written no files, its stdout closing message
  is preserved rather than discarded in favor of trailing stderr tool progress.
- Distinct stream truncation keeps stdout and stderr separate, taking stdout tail over stderr.
- The closing line appears in the halt output, in the structured halt payload (`reason` and `output_tail`),
  and in the retry failure context (`task_failure`).
- Raw coder output is NOT placed in the audit log.
- Timeout branch prioritizes stdout over stderr for timeout output tail.
"""

import subprocess
from pathlib import Path
from unittest import mock
import pytest

from snodo.coders.agy_adapter import AGYAdapter
from snodo.coders.base import LLMCallError
from snodo.coders.opencode_cli_adapter import OpenCodeCLIAdapter
from snodo.compiler.models import Mode, Protocol, Validator
from snodo.core.interfaces import Task, TaskSpec
from snodo.engine.loop import GraphBuilder
from snodo.tools.git import GitMCP
from snodo.tools.workspace import WorkspaceMCP


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / ".gitignore").write_text(".snodo/\n")
    (root / "README.md").write_text("# Test Workspace\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
    return root


def test_zero_write_run_preserves_stdout_over_stderr_in_halt_payload(temp_workspace: Path, capsys, monkeypatch):
    """A coder that exits zero without writing files preserves stdout closing message locally without putting it in audit log or session checkpoint wire surface."""
    from snodo.infrastructure.session import SessionManager
    from snodo.infrastructure.state import ProjectState, write_state
    from snodo.cli.commands.task_cmd import task_show_command
    from types import SimpleNamespace

    protocol = Protocol(
        protocol_id="test-proto",
        name="test-proto",
        initial_mode="producer",
        modes=[
            Mode(mode_id="producer", name="producer", coder="agy"),
        ],
        validators=[
            Validator(
                validator_id="v1",
                validator_type="test",
                description="test validator",
                prompt="test",
            )
        ],
    )
    task = Task(id="task-123", spec="Refactor module X")
    adapter = AGYAdapter(workspace=temp_workspace)

    distinctive_stdout = (
        "I investigated the code and decided no changes are needed because the "
        "requested abstraction already exists in helper.py."
    )
    trailing_stderr_noise = (
        "tool_progress: reading helper.py\n"
        "tool_progress: parsing ast\n"
        "tool_progress: (const path of SCREEN_PATHS) {"
    )

    def fake_run_no_files(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=["agy"],
            returncode=0,
            stdout=distinctive_stdout,
            stderr=trailing_stderr_noise,
        )

    audited_events: list[tuple[str, dict]] = []

    class MockAuditLog:
        def append_event(self, event_type, data):
            audited_events.append((event_type, data))

    mock_audit = MockAuditLog()
    monkeypatch.setenv("SNODO_HOME", str(temp_workspace / ".snodo"))
    write_state(str(temp_workspace), ProjectState(current_mode="producer"))
    session_mgr = SessionManager(audit_log=mock_audit)
    session = session_mgr.create_session("producer", str(temp_workspace))

    with mock.patch.object(adapter, "_run_subprocess", side_effect=fake_run_no_files):
        builder = GraphBuilder(
            protocol=protocol,
            workspace_mcp=WorkspaceMCP(str(temp_workspace)),
            git_mcp=GitMCP(str(temp_workspace)),
            coder=adapter,
            project_root=str(temp_workspace),
            session_manager=session_mgr,
            session_id=session.session_id,
            audit_log=mock_audit,
            job_id="job-1",
        )

        # Create job directory so job state.json is written
        job_dir = temp_workspace / ".snodo" / "jobs" / "job-1"
        job_dir.mkdir(parents=True, exist_ok=True)

        state = {
            "task": task.model_dump(),
            "current_mode": "producer",
            "validation_token": {"jwt": "valid_token"},
            "is_blocked": False,
            "artifacts": [],
            "metadata": {},
            "summary": "",
        }
        with mock.patch.object(builder._token_issuer, "verify_token", return_value=True):
            with mock.patch.object(builder._token_issuer, "consume_token"):
                result = builder._execute_node(state)
                blocked_res = builder._blocked_node(result)

    # Must be blocked as no_file_operations
    assert result["is_blocked"] is True
    assert result["halt_type"] == "no_file_operations"

    # 1. Local halt payload carries output_tail
    payload = blocked_res["metadata"]["halt_payload"]
    assert payload["status"] == "blocked"
    assert payload["halt_type"] == "blocker"
    assert payload["raw_halt_type"] == "blocker"
    assert payload.get("output_tail") == distinctive_stdout

    # 2. Local job state.json carries output_tail
    import json
    job_state = json.loads((job_dir / "state.json").read_text())
    assert job_state["halt"]["output_tail"] == distinctive_stdout

    # 3. Session checkpoint on wire does NOT carry output_tail
    reloaded_session = session_mgr.load_session(session.session_id)
    checkpoint_halt = reloaded_session.checkpoint.decisions.get("halt", {}).get("task-123", {})
    assert "output_tail" not in checkpoint_halt

    # 4. No audit event contains the coder's closing text
    assert len(audited_events) > 0
    for ev_type, ev_data in audited_events:
        ev_str = json.dumps(ev_data, default=str)
        assert distinctive_stdout not in ev_str, f"Audit event {ev_type} leaked coder stdout: {ev_str}"

    # 5. snodo task show displays the coder closing text recovered from job state
    args = SimpleNamespace(task_id="task-123", json=False)
    with mock.patch("snodo.cli.commands.task_cmd.resolve_project_root", return_value=str(temp_workspace)):
        ret = task_show_command(args)
    assert ret == 0
    captured = capsys.readouterr().out
    assert distinctive_stdout in captured


def test_timeout_run_preserves_stdout_closing_message(temp_workspace: Path):
    """Timeout branch takes stdout tail rather than stderr progress noise."""
    adapter = OpenCodeCLIAdapter(workspace=temp_workspace)
    spec = TaskSpec(description="Analyze workspace", constraints=[])

    stdout_msg = "Final coder thought: Found loop in graph; cannot resolve cleanly."
    stderr_noise = "stderr log 1\nstderr log 2\nstderr tail noise"

    def fake_run_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=["opencode"],
            timeout=1800,
            output=stdout_msg,
            stderr=stderr_noise,
        )

    with mock.patch.object(adapter, "_run_subprocess", side_effect=fake_run_timeout):
        with pytest.raises(LLMCallError) as exc_info:
            adapter.implement(spec)

    err_msg = str(exc_info.value)
    assert stdout_msg in err_msg
    assert "stderr tail noise" not in err_msg
    assert adapter.last_timeout_tail == stdout_msg


def test_zero_write_fallback_to_stderr_when_stdout_empty(temp_workspace: Path):
    """When stdout is empty on zero-write run, stderr tail is used as fallback."""
    adapter = AGYAdapter(workspace=temp_workspace)
    spec = TaskSpec(description="Task", constraints=[])

    stderr_error = "Internal plugin error: failed to initialize ast parser"

    def fake_run_no_files(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=["agy"],
            returncode=0,
            stdout="",
            stderr=stderr_error,
        )

    with mock.patch.object(adapter, "_run_subprocess", side_effect=fake_run_no_files):
        artifact = adapter.implement(spec)

    assert artifact.files == []
    assert artifact.metadata.get("output_tail") == stderr_error
    assert adapter.last_output_tail == stderr_error


def test_task_show_formats_reason_and_output_tail(temp_workspace: Path, capsys):
    """snodo task show displays reason containing coder explanation."""
    from snodo.cli.commands.task_cmd import task_show_command
    from unittest.mock import MagicMock

    args = MagicMock()
    args.task_id = "t1"
    args.json = False

    fake_session = MagicMock()
    fake_session.session_id = "sess-1"
    fake_session.mode = "dev"
    fake_session.checkpoint.decisions = {
        "halt": {
            "t1": {
                "task_id": "t1",
                "final_decision": "blocker",
                "halt_type": "blocker",
                "phase": "execute",
                "reason": "Coder produced no file operations: Found already implemented.",
                "output_tail": "Found already implemented.",
                "hint": "Revise the task spec.",
            }
        }
    }

    with mock.patch("snodo.cli.commands.task_cmd.resolve_project_root", return_value=str(temp_workspace)):
        with mock.patch("snodo.infrastructure.state.read_state") as mock_state:
            mock_state.return_value.current_mode = "dev"
            with mock.patch("snodo.infrastructure.session.SessionManager.get_active_session", return_value=fake_session):
                ret = task_show_command(args)

    assert ret == 0
    captured = capsys.readouterr().out
    assert "Coder produced no file operations: Found already implemented." in captured


def test_retry_preserves_coder_explanation_in_prompt(temp_workspace: Path, monkeypatch):
    """Retrying after no_file_operations includes previous attempt's coder reason in retry prompt."""
    from types import SimpleNamespace
    from snodo.infrastructure.session import SessionManager
    from snodo.infrastructure.state import ProjectState, write_state
    from snodo.protocols import _TEMPLATE_PROTOCOLS
    from snodo.cli.commands.run_cmd import _retry_task

    protocol = _TEMPLATE_PROTOCOLS["solo"]
    mode = protocol.modes[0].mode_id
    write_state(str(temp_workspace), ProjectState(current_mode=mode))

    session_mgr = SessionManager(sessions_dir=temp_workspace / ".snodo" / "sessions")
    session = session_mgr.create_session(mode, str(temp_workspace))

    # Persist task_failure with coder explanation in justification
    session_mgr.update_decision(session.session_id, "task_failure", {
        "t1": {
            "spec": "original task spec",
            "original_spec": "original task spec",
            "branch": "task/t1",
            "attempt": 1,
            "phase": "execute",
            "failed_validators": [
                {
                    "validator_id": "no_file_operations",
                    "severity": "blocker",
                    "justification": "Coder produced no file operations: Refused due to ambiguity in helper.py.",
                }
            ],
            "files_changed": [],
        }
    })

    monkeypatch.setattr("snodo.cli.commands.run_cmd.load_protocol", lambda path: protocol)
    dispatched_tasks = []

    def mock_execute_task(args, prot, t, m):
        dispatched_tasks.append((t.id, t.spec))
        return 0

    monkeypatch.setattr("snodo.cli.commands.run_cmd._execute_task", mock_execute_task)

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml",
        model="mock-model",
        description="revised task spec",
    )

    ret = _retry_task(args, "t1", str(temp_workspace), session_mgr)
    assert ret == 0
    assert len(dispatched_tasks) == 1
    retry_spec = dispatched_tasks[0][1]
    assert "Coder produced no file operations: Refused due to ambiguity in helper.py." in retry_spec

