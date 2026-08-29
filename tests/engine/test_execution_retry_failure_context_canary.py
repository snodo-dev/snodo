"""Canary test for Issue #116: Coder execution faults must write failure context so --retry works.

FILE: tests/engine/test_execution_retry_failure_context_canary.py
"""

from types import SimpleNamespace

from snodo.core.interfaces import ExecutionError, Task
from snodo.engine.loop import GraphBuilder
from snodo.infrastructure.session import SessionManager
from snodo.infrastructure.state import ProjectState, write_state
from snodo.protocols import _TEMPLATE_PROTOCOLS

from snodo.cli.commands.run_cmd import _retry_task


def test_execution_fault_retry_canary(tmp_path, monkeypatch):
    """Canary test: Force a coder execution fault during task execution,
    assert that failure context is written to session, and assert that
    retrying the task via _retry_task finds the failure context and proceeds.
    """
    project_root = str(tmp_path)
    protocol = _TEMPLATE_PROTOCOLS["solo"]
    mode = protocol.modes[0].mode_id
    write_state(project_root, ProjectState(current_mode=mode))

    session_mgr = SessionManager(sessions_dir=tmp_path / ".snodo" / "sessions")
    session = session_mgr.create_session(mode, project_root)

    task = Task(id="task_canary123", spec="Implement feature with missing credentials")

    # Mock coder function to raise ExecutionError (missing credentials)
    def failing_coder(*args, **kwargs):
        raise ExecutionError("Provider API error: missing API credentials")

    builder = GraphBuilder(
        protocol=protocol,
        session_manager=session_mgr,
        session_id=session.session_id,
        project_root=project_root,
        executor_fn=failing_coder,
    )

    graph = builder.build_graph().compile()
    init_state = {
        "task": task.model_dump(),
        "current_mode": mode,
        "iteration": 0,
        "stage": "governance",
        "validation_results": [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {},
        "messages": [],
        "summary": "",
    }
    result = graph.invoke(init_state)

    assert result["is_blocked"] is True
    assert result["halt_type"] == "internal_error"

    # Verify that failure context was written to session decisions
    session_reloaded = session_mgr.load_session(session.session_id)
    task_failure = session_reloaded.checkpoint.decisions.get("task_failure", {})
    assert "task_canary123" in task_failure, (
        "Failure context for task_canary123 must be written on execution fault"
    )

    failure_data = task_failure["task_canary123"]
    assert failure_data["spec"] == "Implement feature with missing credentials"
    assert failure_data["attempt"] == 1
    assert len(failure_data["failed_validators"]) > 0
    assert "missing API credentials" in failure_data["failed_validators"][0]["justification"]

    # Now verify that _retry_task can successfully load the context and retry
    retry_args = SimpleNamespace(
        protocol=".snodo/protocol.yml",
        model="mock-model",
        description="revised spec after credentials fix",
    )

    # Patch load_protocol to return our protocol
    monkeypatch.setattr("snodo.cli.commands.run_cmd.load_protocol", lambda path: protocol)

    # Mock _execute_task to simulate successful retry execution
    executed = []
    def mock_execute_task(args, prot, t, m):
        executed.append((t.id, t.spec))
        return 0

    monkeypatch.setattr("snodo.cli.commands.run_cmd._execute_task", mock_execute_task)

    res = _retry_task(retry_args, "task_canary123", project_root, session_mgr)
    assert res == 0
    assert len(executed) == 1
    assert executed[0][0] == "task_canary123"
    assert "revised spec after credentials fix" in executed[0][1]
