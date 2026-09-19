"""Tests for terminal outcome and halt type audit log recording.

FILE: tests/engine/test_terminal_outcome_audit.py

Asserts that:
1. Every task ending in each coarse outcome it exercises (escalate, blocker,
   validator_error, internal_error) produces an audit event naming that outcome.
2. The specific halt type survives alongside the canonical coarse outcome in
   `raw_halt_type` so the specific cause (e.g. no_file_operations, head_not_moved,
   turn_budget_exhausted, recovery_exhausted, execution_error, constraint)
   is not lost.
"""

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from snodo.compiler.models import Mode, Protocol, Validator
from snodo.core.interfaces import Task
from snodo.engine.loop import GraphBuilder
from snodo.infrastructure.audit import AuditLog

_SPEC_PATH = Path(__file__).resolve().parents[2] / "docs" / "specs" / "cloud-sync.md"


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
        # final_decision was a legacy duplicate of halt_type on the halt event
        # and has been retired; halt_type is the single canonical outcome.
        assert "final_decision" not in data

    def test_completed_task_leaves_task_complete_audit_event(self):
        """A completed task records its committed SHA in the audit log."""
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
            "metadata": {"commit": "a" * 40},
            "messages": [],
        }

        builder._complete_node(state)

        audit.append_event.assert_called_once()
        event_type, data = audit.append_event.call_args[0]
        assert event_type == "task_complete"
        assert data["op"] == "task_complete"
        assert data["task_ref"] == "task_001"
        assert data["artifacts"] == ["src/app.py"]
        assert data["commit"] == "a" * 40

    def test_completed_task_without_commit_records_null_commit(self):
        """A completed task with no commit has explicit null provenance."""
        protocol = _make_protocol()
        audit = MagicMock(spec=AuditLog)
        builder = GraphBuilder(protocol, audit_log=audit)

        state = _make_loop_state_dict()
        state.update({
            "is_complete": True,
            "is_blocked": False,
            "stage": "move_next",
        })

        builder._complete_node(state)

        _, data = audit.append_event.call_args[0]
        assert data["commit"] is None

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
            ("turn_budget_exhausted", "environment_error"),
            ("execution_error", "environment_error"),
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
        assert data["raw_halt_type"] == raw_halt
        assert "final_decision" not in data


def _documented_event_keys(event_type: str):
    """The `data` keys the cloud-sync spec documents for *event_type*."""
    for line in _SPEC_PATH.read_text().splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0].strip("`") == event_type:
            return {k.strip().strip("`") for k in cells[1].split(",")}
    raise AssertionError(f"no `{event_type}` row found in cloud-sync.md")


def _documented_halt_keys():
    """The `data` keys the cloud-sync spec documents for the halt event."""
    return _documented_event_keys("halt")


class TestHaltEventShape:
    def test_emitted_halt_keys_match_documented_shape(self):
        """The keys on the emitted halt event are exactly the documented shape
        (minus the ``op`` routing field present on every event)."""
        protocol = _make_protocol()
        audit = MagicMock(spec=AuditLog)
        builder = GraphBuilder(protocol, audit_log=audit)

        state = _make_loop_state_dict(
            halt_type="blocked",
            violations=["a blocker was raised"],
            results=[
                {"validator_id": "v1", "severity": "blocker", "justification": "no"},
            ],
        )
        builder._blocked_node(state)

        _, data = audit.append_event.call_args[0]
        emitted = set(data) - {"op"}
        assert emitted == _documented_halt_keys()


class TestTaskCompleteEventShape:
    def test_emitted_task_complete_keys_match_documented_shape(self):
        """The keys on the emitted task_complete event are exactly the
        documented shape (minus ``op``). The change-size field rides the
        published interface, so this test is what refuses a silent change
        to the completed-task record (#377)."""
        protocol = _make_protocol()
        audit = MagicMock(spec=AuditLog)
        builder = GraphBuilder(protocol, audit_log=audit, session_id="sess_shape")

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

        _, data = audit.append_event.call_args[0]
        emitted = set(data) - {"op"}
        assert emitted == _documented_event_keys("task_complete")
        assert data["change_size"] is None  # no git here: unmeasured, not zero
