"""Tests for terminal outcome and halt type audit log recording.

FILE: tests/engine/test_terminal_outcome_audit.py

Asserts that:
1. Every task ending in each of the four coarse outcomes (escalate, blocker,
   validator_error, internal_error) produces an audit event naming that outcome.
2. The specific halt type survives alongside the canonical coarse outcome in
   `raw_halt_type` so the specific cause (e.g. no_file_operations, head_not_moved,
   turn_budget_exhausted, recovery_exhausted, execution_error, constraint)
   is not lost.
"""

from unittest.mock import MagicMock
import pytest

from snodo.compiler.models import Mode, Protocol, Validator
from snodo.core.interfaces import Task
from snodo.engine.loop import GraphBuilder
from snodo.infrastructure.audit import AuditLog


def _make_protocol():
    return Protocol(
        protocol_id="test_protocol",
        name="Test Protocol",
        modes=[Mode(mode_id="producer", name="Producer", tools=["edit"], validators=["v1"])],
        validators=[Validator(validator_id="v1", validator_type="security")],
        initial_mode="producer",
    )


def _make_task(tid="task_001", spec="Implement feature"):
    return Task(id=tid, spec=spec)


def _make_loop_state_dict(task=None, halt_type=None, violations=None, results=None):
    t = task or _make_task()
    return {
        "task": {"id": t.id, "spec": t.spec},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "validate",
        "validation_results": results or [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": False,
        "constraint_violations": violations or [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": True,
        "halt_type": halt_type,
        "metadata": {},
        "messages": [],
    }


class TestTerminalOutcomeAudit:
    @pytest.mark.parametrize(
        ("halt_type_input", "expected_outcome"),
        [
            ("escalated", "escalate"),
            ("blocked", "blocker"),
            ("constraint", "blocker"),
            ("validator_error", "validator_error"),
            ("internal_error", "internal_error"),
        ],
    )
    def test_task_ending_in_each_outcome_leaves_audit_event(
        self, halt_type_input, expected_outcome
    ):
        """Every terminal non-success outcome records an audit event naming that outcome."""
        protocol = _make_protocol()
        audit = MagicMock(spec=AuditLog)
        builder = GraphBuilder(protocol, audit_log=audit)

        state = _make_loop_state_dict(
            halt_type=halt_type_input,
            violations=[f"Violation for {halt_type_input}"],
            results=[{"validator_id": "v1", "severity": "blocker", "justification": "fail"}],
        )

        builder._blocked_node(state)

        audit.append_event.assert_called_once()
        event_type, data = audit.append_event.call_args[0]
        assert event_type == "halt"
        assert data["op"] == "halt"
        assert data["task_ref"] == "task_001"
        assert data["halt_type"] == expected_outcome
        assert data["final_decision"] == expected_outcome

    def test_completed_task_leaves_task_complete_audit_event(self):
        """A completed task records task_complete in the audit log."""
        protocol = _make_protocol()
        audit = MagicMock(spec=AuditLog)
        builder = GraphBuilder(protocol, audit_log=audit)

        state = {
            "task": {"id": "task_001", "spec": "Implement feature"},
            "current_mode": "producer",
            "iteration": 1,
            "stage": "move_next",
            "validation_results": [],
            "validation_token": None,
            "artifacts": ["src/app.py"],
            "constraints_passed": True,
            "constraint_violations": [],
            "policy_decision": None,
            "is_complete": True,
            "is_blocked": False,
            "halt_type": None,
            "metadata": {},
            "messages": [],
        }

        builder._complete_node(state)

        audit.append_event.assert_called_once()
        event_type, data = audit.append_event.call_args[0]
        assert event_type == "task_complete"
        assert data["op"] == "task_complete"
        assert data["task_ref"] == "task_001"
        assert data["artifacts"] == ["src/app.py"]

    @pytest.mark.parametrize(
        ("raw_halt", "expected_coarse"),
        [
            ("escalated", "escalate"),
            ("blocked", "blocker"),
            ("validator_error", "validator_error"),
            ("internal_error", "internal_error"),
            ("constraint", "blocker"),
            ("wf3", "blocker"),
            ("max_iterations", "blocker"),
            ("turn_budget_exhausted", "blocker"),
            ("execution_error", "blocker"),
            ("recovery_exhausted", "blocker"),
            ("recovery_stalled", "blocker"),
            ("head_not_moved", "blocker"),
            ("no_file_operations", "blocker"),
        ],
    )
    def test_specific_halt_type_survives_alongside_coarse_outcome(
        self, raw_halt, expected_coarse
    ):
        """The specific raw halt type survives alongside the canonical coarse outcome."""
        protocol = _make_protocol()
        audit = MagicMock(spec=AuditLog)
        builder = GraphBuilder(protocol, audit_log=audit)

        state = _make_loop_state_dict(
            halt_type=raw_halt,
            violations=[f"Halted due to {raw_halt}"],
        )

        builder._blocked_node(state)

        audit.append_event.assert_called_once()
        _, data = audit.append_event.call_args[0]
        assert data["halt_type"] == expected_coarse
        assert data["final_decision"] == expected_coarse
        assert data["raw_halt_type"] == raw_halt
