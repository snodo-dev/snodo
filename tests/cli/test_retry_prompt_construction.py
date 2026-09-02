"""Tests for clean retry prompt construction without wrapping (Fixes #193).

FILE: tests/cli/test_retry_prompt_construction.py
"""

from types import SimpleNamespace

from snodo.cli.commands.run_cmd import _retry_task
from snodo.infrastructure.session import SessionManager
from snodo.infrastructure.state import ProjectState, write_state
from snodo.protocols import _TEMPLATE_PROTOCOLS


def _setup_retry_session(tmp_path, monkeypatch):
    project_root = str(tmp_path)
    protocol = _TEMPLATE_PROTOCOLS["solo"]
    mode = protocol.modes[0].mode_id
    write_state(project_root, ProjectState(current_mode=mode))

    session_mgr = SessionManager(sessions_dir=tmp_path / ".snodo" / "sessions")
    session = session_mgr.create_session(mode, project_root)

    monkeypatch.setattr(
        "snodo.cli.commands.run_cmd.load_protocol", lambda path: protocol
    )
    executed_tasks = []

    def mock_execute_task(args, prot, t, m):
        executed_tasks.append(t)
        return 0

    monkeypatch.setattr("snodo.cli.commands.run_cmd._execute_task", mock_execute_task)

    return project_root, session_mgr, session, executed_tasks


def test_retry_prompt_no_wrapping_across_multiple_attempts(tmp_path, monkeypatch):
    """Attempt 3 of 3 does not accumulate nested 'Original spec:' headers or stale failures."""
    project_root, session_mgr, session, executed = _setup_retry_session(tmp_path, monkeypatch)

    # Simulate attempt 2 having failed with failure context recording attempt 2
    session_mgr.update_decision(session.session_id, "task_failure", {
        "task_abc": {
            "spec": "add a farewell() function to src/index.js",
            "original_spec": "add a farewell() function to src/index.js",
            "branch": "task/task_abc/add-a-farewell-function",
            "attempt": 2,
            "phase": "execute",
            "failed_validators": [
                {
                    "validator_id": "execution_error",
                    "severity": "blocker",
                    "justification": "coder invocation error: token limit exceeded",
                }
            ],
            "files_changed": [],
        }
    })

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml",
        model="mock-model",
        description="add a farewell() function to src/index.js and test it",
    )

    res = _retry_task(args, "task_abc", project_root, session_mgr)
    assert res == 0
    assert len(executed) == 1
    task = executed[0]

    expected = (
        "Original spec: add a farewell() function to src/index.js\n\n"
        "Revised spec (replaces original): add a farewell() function to src/index.js and test it\n\n"
        "Previous attempt 2 failed at execute:\n"
        "  execution_error: coder invocation error: token limit exceeded\n\n"
        "Fix the issues above."
    )

    assert task.spec == expected
    # The authoritative root_spec is preserved for downstream tools and branch naming
    assert task.root_spec == "add a farewell() function to src/index.js and test it"
    # No doubled "Original spec:"
    assert task.spec.count("Original spec:") == 1
    # No doubled "Revised spec"
    assert task.spec.count("Revised spec (replaces original):") == 1
    # No dangling "Files changed"
    assert "Files changed" not in task.spec


def test_retry_prompt_without_revised_spec(tmp_path, monkeypatch):
    """Retry without a revised spec keeps original spec and only includes relevant failure context."""
    project_root, session_mgr, session, executed = _setup_retry_session(tmp_path, monkeypatch)

    session_mgr.update_decision(session.session_id, "task_failure", {
        "task_xyz": {
            "spec": "implement health check endpoint",
            "original_spec": "implement health check endpoint",
            "branch": "task/task_xyz/implement-health-check",
            "attempt": 1,
            "phase": "pre_execute",
            "failed_validators": [
                {
                    "validator_id": "spec_check",
                    "severity": "blocker",
                    "justification": "missing acceptance criteria",
                }
            ],
            "files_changed": [],
        }
    })

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml",
        model="mock-model",
        description=None,
    )

    res = _retry_task(args, "task_xyz", project_root, session_mgr)
    assert res == 0
    assert len(executed) == 1
    task = executed[0]

    expected = (
        "Original spec: implement health check endpoint\n\n"
        "Previous attempt 1 failed pre-validation:\n"
        "  spec_check: missing acceptance criteria\n\n"
        "Fix the issues above."
    )
    assert task.spec == expected
    assert task.root_spec == "implement health check endpoint"
    assert "Files changed" not in task.spec
    assert "Revised spec" not in task.spec


def test_retry_prompt_with_files_changed(tmp_path, monkeypatch):
    """When previous attempt changed files, they are listed cleanly without dangling empty lines."""
    project_root, session_mgr, session, executed = _setup_retry_session(tmp_path, monkeypatch)

    session_mgr.update_decision(session.session_id, "task_failure", {
        "task_files": {
            "spec": "refactor auth logic",
            "original_spec": "refactor auth logic",
            "branch": "task/task_files/refactor-auth-logic",
            "attempt": 1,
            "phase": "post_execute",
            "failed_validators": [
                {
                    "validator_id": "quality",
                    "severity": "blocker",
                    "justification": "unit tests failed",
                }
            ],
            "files_changed": ["src/auth.py", "tests/test_auth.py"],
        }
    })

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml",
        model="mock-model",
        description=None,
    )

    res = _retry_task(args, "task_files", project_root, session_mgr)
    assert res == 0
    assert len(executed) == 1
    task = executed[0]

    expected = (
        "Original spec: refactor auth logic\n\n"
        "Previous attempt 1 failed post-validation:\n"
        "  quality: unit tests failed\n\n"
        "Files changed in previous attempt: src/auth.py, tests/test_auth.py\n\n"
        "Fix the issues above."
    )
    assert task.spec == expected
