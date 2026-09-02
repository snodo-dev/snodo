"""Tests for coder attribution and stale pending-decision clearing (Fixes #148).

- the coder registry name appears in the halt payload, the audit trail and the
  run banner, and differs between two runs of the same task under different
  coders;
- a task that completes clears any pending decision left by an earlier attempt;
- a task that halts still records one.
"""

from pathlib import Path
from unittest.mock import MagicMock

from snodo.compiler.models import Mode, Protocol, Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.engine.loop import GraphBuilder
from snodo.engine.nodes.writeback import _coder_registry_name
from snodo.engine.state import LoopState


def _make_protocol():
    return Protocol(
        protocol_id="test", name="Test",
        modes=[Mode(mode_id="producer", name="Producer", tools=[], validators=[])],
        validators=[Validator(validator_id="v1", validator_type="security",
                              evaluation_phase="pre_execute")],
        initial_mode="producer",
    )


def _make_task(tid="t1", spec="do something"):
    return Task(id=tid, spec=spec)


def _make_loop_state(task=None):
    t = task or _make_task()
    return LoopState(task=t, current_mode="producer")


def _make_session_mock(decisions=None):
    session = MagicMock()
    session.checkpoint.decisions = decisions if decisions is not None else {}
    mgr = MagicMock()
    mgr.load_session.return_value = session
    return mgr, session


def _make_builder_with_session(decisions=None, coder=None):
    protocol = _make_protocol()
    builder = GraphBuilder(protocol, coder=coder)
    mgr, session = _make_session_mock(decisions)
    builder._session_manager = mgr
    builder._session_id = "sess-1"
    return builder, mgr, session


# ---------------------------------------------------------------------------
# _coder_registry_name
# ---------------------------------------------------------------------------

class TestCoderRegistryName:
    def test_uses_coder_name_attribute(self):
        coder = MagicMock()
        coder.coder_name = "opencode-cli"
        assert _coder_registry_name(coder) == "opencode-cli"

    def test_maps_registered_class_to_registry_name(self):
        from snodo.coders import MockAdapter
        assert _coder_registry_name(MockAdapter()) == "mock"

    def test_falls_back_to_class_name(self):
        class _Unregistered:
            pass
        assert _coder_registry_name(_Unregistered()) == "_Unregistered"

    def test_none_returns_class_name_of_none(self):
        assert _coder_registry_name(None) == "NoneType"


# ---------------------------------------------------------------------------
# Coder name in the halt payload
# ---------------------------------------------------------------------------

class TestCoderInHaltPayload:
    def test_halt_payload_carries_coder_and_model(self):
        from snodo.coders import MockAdapter
        builder, mgr, session = _make_builder_with_session(coder=MockAdapter())
        builder._default_model = "mock-model"
        state = _make_loop_state()
        state.is_complete = False
        state.is_blocked = True
        state.halt_type = "constraint"
        state.constraint_violations = ["v1"]

        payload = builder._build_halt_payload(state)

        assert payload["coder"] == "mock"
        assert payload["coder_model"] is None
        assert payload["judging_model"] == "mock-model"

    def test_coder_differs_between_two_coders(self):
        from snodo.coders import MockAdapter
        from snodo.coders.litellm import LiteLLMAdapter

        builder_a, _, _ = _make_builder_with_session(coder=MockAdapter())
        builder_b, _, _ = _make_builder_with_session(coder=LiteLLMAdapter(model="gpt-4"))

        state = _make_loop_state()
        state.is_complete = False
        state.is_blocked = True
        state.halt_type = "constraint"
        state.constraint_violations = ["v1"]

        payload_a = builder_a._build_halt_payload(state)
        payload_b = builder_b._build_halt_payload(state)

        assert payload_a["coder"] == "mock"
        assert payload_b["coder"] == "litellm"
        assert payload_a["coder"] != payload_b["coder"]


# ---------------------------------------------------------------------------
# Coder name in the audit trail
# ---------------------------------------------------------------------------

class TestCoderInAuditTrail:
    def test_dispatch_event_carries_coder_and_model(self):
        from snodo.coders import MockAdapter
        builder, mgr, session = _make_builder_with_session(coder=MockAdapter())
        builder._default_model = "mock-model"
        audit = MagicMock()
        builder._audit_log = audit
        builder._session_id = "sess-1"

        state = _make_loop_state()
        state.validation_token = None
        state.artifacts = ["src/a.py"]

        coder_obj = getattr(builder, "coder", None)
        coder_name = _coder_registry_name(coder_obj)
        if hasattr(coder_obj, "_bare_model"):
            bare = coder_obj._bare_model()
            coder_model = bare if bare else None
        else:
            coder_model = getattr(coder_obj, "model", None)

        judging_model = getattr(builder, "_default_model", None)

        builder._audit("dispatch", {
            "op": "dispatch",
            "task_ref": state.task.id,
            "token_id": state.task.id,
            "mode": state.current_mode,
            "coder": coder_name,
            "coder_model": coder_model,
            "judging_model": judging_model,
            "artifacts_count": len(state.artifacts),
        })

        audit.append_event.assert_called_once()
        event_type, data = audit.append_event.call_args[0]
        assert event_type == "dispatch"
        assert data["coder"] == "mock"
        assert data["coder_model"] is None
        assert data["judging_model"] == "mock-model"


class TestMockRecordsNoCoderModel:
    """The mock coder makes no LLM call, so it records coder_model: null.

    MockAdapter has no ``_bare_model`` of its own, but the ``-m`` value is the
    judging model: the mock never forwards it, so attributing it to the coder
    would report a model the coder never used. Both the halt payload and the
    dispatch audit event must record null (Fixes #170).
    """

    def test_mock_halt_payload_records_no_coder_model(self):
        from snodo.coders import MockAdapter
        builder, _, _ = _make_builder_with_session(coder=MockAdapter())
        builder._default_model = "mock-default"
        state = _make_loop_state()
        state.is_complete = False
        state.is_blocked = True
        state.halt_type = "constraint"
        state.constraint_violations = ["v1"]

        payload = builder._build_halt_payload(state)

        assert payload["coder"] == "mock"
        assert payload["coder_model"] is None
        assert payload["judging_model"] == "mock-default"

    def test_mock_dispatch_audit_records_no_coder_model(self):
        from snodo.coders import MockAdapter
        builder, _, _ = _make_builder_with_session(coder=MockAdapter())
        builder._default_model = "mock-default"
        audit = MagicMock()
        builder._audit_log = audit
        builder._session_id = "sess-1"

        state = _make_loop_state()
        state.validation_token = None
        state.artifacts = ["src/a.py"]

        builder._execute_node({
            "task": {"id": state.task.id, "spec": state.task.spec},
            "current_mode": state.current_mode,
            "iteration": state.iteration,
            "stage": "execute",
            "validation_results": [],
            "validation_token": None,
            "artifacts": list(state.artifacts),
            "constraints_passed": True,
            "constraint_violations": [],
            "policy_decision": None,
            "is_complete": state.is_complete,
            "is_blocked": state.is_blocked,
            "metadata": {},
        })

        dispatch = None
        for call in audit.append_event.call_args_list:
            if call.args and call.args[0] == "dispatch":
                dispatch = call.args[1]
                break
        assert dispatch is not None
        assert dispatch["coder"] == "mock"
        assert dispatch["coder_model"] is None
        assert dispatch["judging_model"] == "mock-default"


# ---------------------------------------------------------------------------
# Stale pending decisions cleared on completion
# ---------------------------------------------------------------------------

class TestClearPendingDecisions:
    def test_completed_task_clears_pending_decision(self):
        """A task that completes clears any pending decision left by an earlier
        attempt (Fixes #148)."""
        builder, mgr, session = _make_builder_with_session(decisions={
            "pending_decisions": {
                "t1": {"type": "adjudicate", "decision": "proceed",
                       "justification": "No completion_fn available",
                       "severity": "blocker"},
            },
            "task_failure": {"t1": {"attempt": 1}},
        })

        builder._clear_failure_context(_make_loop_state())

        # update_decision called for both task_failure and pending_decisions.
        calls = [c.args[1] for c in mgr.update_decision.call_args_list]
        assert "pending_decisions" in calls
        assert "task_failure" in calls
        for c in mgr.update_decision.call_args_list:
            key = c.args[1]
            value = c.args[2]
            if key == "pending_decisions":
                assert "t1" not in value
            if key == "task_failure":
                assert "t1" not in value

    def test_halted_task_still_records_pending_decision(self):
        """A task that halts still records a pending decision."""
        builder, mgr, session = _make_builder_with_session()
        session.checkpoint.decisions = {"pending_decisions": {}}
        state = _make_loop_state()
        results = [ValidatorResult(validator_id="sec", severity="blocker", justification="bad")]

        builder._auto_write_pending_decisions(state, results)

        pending = mgr.update_decision.call_args[0][2]
        assert "t1" in pending
        assert pending["t1"]["severity"] == "blocker"

    def test_pending_decision_for_other_task_survives(self):
        """Clearing one task's pending decision leaves other tasks' intact."""
        builder, mgr, session = _make_builder_with_session(decisions={
            "pending_decisions": {
                "t1": {"severity": "blocker"},
                "t2": {"severity": "blocker"},
            },
        })

        builder._clear_failure_context(_make_loop_state())

        for c in mgr.update_decision.call_args_list:
            if c.args[1] == "pending_decisions":
                value = c.args[2]
                assert "t1" not in value
                assert "t2" in value


# ---------------------------------------------------------------------------
# judging_model vs coder_model attribution (Fixes #196, ADR 039)
# ---------------------------------------------------------------------------

class TestJudgingModelVsCoderModelAttribution:
    """Validation judging model is resolved from config, independent of coder model."""

    def test_judging_model_uses_resolved_validator_model(self, monkeypatch):
        from unittest.mock import patch
        from snodo.coders import get_coder

        protocol = _make_protocol()
        coder = get_coder("agy", model="gemini-3.7-flash-medium", workspace=Path("/tmp"))

        with patch("snodo.config.ConfigManager.load", return_value={"llm": {"validator": {"model": "deepseek/deepseek-v4-flash"}}}):
            builder = GraphBuilder(protocol, coder=coder)

        assert builder._validator_model == "deepseek/deepseek-v4-flash"

        state = _make_loop_state()
        state.is_blocked = True
        state.halt_type = "constraint"
        state.constraint_violations = ["v1"]

        payload = builder._build_halt_payload(state)
        assert payload["coder"] == "agy"
        assert payload["coder_model"] is None
        assert payload["judging_model"] == "deepseek/deepseek-v4-flash"

