"""Characterization tests for snodo/mcp/decision_handlers.py (18% → ~95%).

Pins DecisionToolHandler._get_active_session, handle_propose_adjudicate,
and handle_propose_set_model — the human-gated decision proposal path.
"""

import pytest
from unittest.mock import patch, MagicMock

from snodo.mcp.decision_handlers import DecisionToolHandler
from snodo.mcp.server import MCPError


def _handler(project_root="/tmp/test_decisions"):
    return DecisionToolHandler(project_root)


def _mock_active_session(session_decisions=None):
    """Return a (session, mgr) pair ready to be used with patch.object."""
    session = MagicMock()
    session.session_id = "sess-active"
    session.checkpoint.decisions = session_decisions if session_decisions is not None else {}
    mgr = MagicMock()
    return session, mgr


# ---------------------------------------------------------------------------
# _get_active_session
# ---------------------------------------------------------------------------

class TestGetActiveSession:
    def test_no_active_session_raises_mcp_error(self):
        handler = _handler()
        with patch("snodo.mcp.decision_handlers.read_state") as mock_rs, \
             patch("snodo.mcp.decision_handlers.SessionManager") as mock_sm:
            mock_rs.return_value = MagicMock(current_mode="producer")
            mock_sm.return_value.get_active_session.return_value = None
            with pytest.raises(MCPError, match="No active session"):
                handler._get_active_session()

    def test_active_session_returned(self):
        handler = _handler()
        mock_session = MagicMock()
        with patch("snodo.mcp.decision_handlers.read_state") as mock_rs, \
             patch("snodo.mcp.decision_handlers.SessionManager") as mock_sm:
            mock_rs.return_value = MagicMock(current_mode="producer")
            mock_sm.return_value.get_active_session.return_value = mock_session
            session, mgr = handler._get_active_session()
        assert session is mock_session

    def test_empty_current_mode_defaults_to_producer(self):
        handler = _handler()
        with patch("snodo.mcp.decision_handlers.read_state") as mock_rs, \
             patch("snodo.mcp.decision_handlers.SessionManager") as mock_sm:
            mock_rs.return_value = MagicMock(current_mode="")
            mock_sm.return_value.get_active_session.return_value = None
            with pytest.raises(MCPError):
                handler._get_active_session()
            # Should use "producer" as default mode
            mock_sm.return_value.get_active_session.assert_called_once()
            call_args = mock_sm.return_value.get_active_session.call_args[0]
            assert call_args[0] == "producer"


# ---------------------------------------------------------------------------
# handle_propose_adjudicate — guard branches
# ---------------------------------------------------------------------------

class TestProposeAdjudicate:
    def test_missing_task_id_raises(self):
        with pytest.raises(MCPError, match="requires task_id"):
            _handler().handle_propose_adjudicate({})

    def test_missing_validator_id_raises(self):
        with pytest.raises(MCPError, match="requires validator_id"):
            _handler().handle_propose_adjudicate({"task_id": "t1"})

    def test_invalid_decision_raises(self):
        with pytest.raises(MCPError, match="decision must be"):
            _handler().handle_propose_adjudicate({
                "task_id": "t1",
                "validator_id": "security",
                "decision": "maybe",
            })

    def test_valid_decision_proceed_accepted(self):
        handler = _handler()
        session, mgr = _mock_active_session()
        with patch.object(handler, "_get_active_session", return_value=(session, mgr)):
            result = handler.handle_propose_adjudicate({
                "task_id": "t1",
                "validator_id": "security",
                "decision": "proceed",
                "justification": "reviewed and ok",
            })
        assert result["status"] == "pending"
        assert result["task_id"] == "t1"

    def test_valid_decision_halt_accepted(self):
        handler = _handler()
        session, mgr = _mock_active_session()
        with patch.object(handler, "_get_active_session", return_value=(session, mgr)):
            result = handler.handle_propose_adjudicate({
                "task_id": "t1",
                "validator_id": "security",
                "decision": "halt",
                "justification": "too risky",
            })
        assert result["status"] == "pending"

    def test_proposal_stored_in_pending_decisions(self):
        handler = _handler()
        session, mgr = _mock_active_session(session_decisions={})
        with patch.object(handler, "_get_active_session", return_value=(session, mgr)):
            handler.handle_propose_adjudicate({
                "task_id": "t1",
                "validator_id": "sec",
                "decision": "proceed",
                "justification": "ok",
            })
        mgr.update_decision.assert_called_once()
        args = mgr.update_decision.call_args[0]
        assert args[0] == "sess-active"
        assert args[1] == "pending_decisions"
        pending = args[2]
        assert "t1" in pending
        assert pending["t1"]["type"] == "adjudicate"
        assert pending["t1"]["validator_id"] == "sec"

    def test_instruction_contains_authorize_command(self):
        handler = _handler()
        session, mgr = _mock_active_session()
        with patch.object(handler, "_get_active_session", return_value=(session, mgr)):
            result = handler.handle_propose_adjudicate({
                "task_id": "task-xyz",
                "validator_id": "sec",
                "decision": "proceed",
                "justification": "ok",
            })
        assert "snodo authorize task-xyz" in result["instruction"]

    def test_pending_not_dict_reset(self):
        """Existing pending_decisions value not a dict → reset to empty {}."""
        handler = _handler()
        session, mgr = _mock_active_session(
            session_decisions={"pending_decisions": "corrupt"}
        )
        with patch.object(handler, "_get_active_session", return_value=(session, mgr)):
            result = handler.handle_propose_adjudicate({
                "task_id": "t1",
                "validator_id": "sec",
                "decision": "proceed",
                "justification": "ok",
            })
        pending = mgr.update_decision.call_args[0][2]
        assert isinstance(pending, dict)
        assert "t1" in pending


# ---------------------------------------------------------------------------
# handle_propose_set_model — guard branches
# ---------------------------------------------------------------------------

class TestProposeSetModel:
    def test_missing_task_id_raises(self):
        with pytest.raises(MCPError, match="requires task_id"):
            _handler().handle_propose_set_model({})

    def test_missing_proposed_model_raises(self):
        with pytest.raises(MCPError, match="requires proposed_model"):
            _handler().handle_propose_set_model({"task_id": "t1"})

    def test_missing_scope_raises(self):
        with pytest.raises(MCPError, match="requires scope"):
            _handler().handle_propose_set_model({
                "task_id": "t1",
                "proposed_model": "gpt-4o",
            })

    def test_success_returns_pending(self):
        handler = _handler()
        session, mgr = _mock_active_session()
        with patch.object(handler, "_get_active_session", return_value=(session, mgr)):
            result = handler.handle_propose_set_model({
                "task_id": "t1",
                "proposed_model": "gpt-4o",
                "scope": "coder",
                "justification": "faster for this task",
            })
        assert result["status"] == "pending"
        assert result["task_id"] == "t1"
        assert result["proposal"]["type"] == "set_model"
        assert result["proposal"]["proposed_model"] == "gpt-4o"
        assert result["proposal"]["scope"] == "coder"

    def test_proposal_stored_with_correct_fields(self):
        handler = _handler()
        session, mgr = _mock_active_session()
        with patch.object(handler, "_get_active_session", return_value=(session, mgr)):
            handler.handle_propose_set_model({
                "task_id": "t1",
                "proposed_model": "claude-opus-4",
                "scope": "validator:security",
                "justification": "better accuracy",
            })
        pending = mgr.update_decision.call_args[0][2]
        assert pending["t1"]["proposed_model"] == "claude-opus-4"
        assert pending["t1"]["scope"] == "validator:security"
        assert pending["t1"]["proposed_by"] == "agent"

    def test_pending_not_dict_reset(self):
        handler = _handler()
        session, mgr = _mock_active_session(
            session_decisions={"pending_decisions": 42}
        )
        with patch.object(handler, "_get_active_session", return_value=(session, mgr)):
            result = handler.handle_propose_set_model({
                "task_id": "t1",
                "proposed_model": "gpt-4o",
                "scope": "coder",
                "justification": "ok",
            })
        pending = mgr.update_decision.call_args[0][2]
        assert isinstance(pending, dict)
