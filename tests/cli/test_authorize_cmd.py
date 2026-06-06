"""Tests for authorize flow and decision proposal handlers.

Mock SessionManager to avoid filesystem dependencies.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from snodo.infrastructure.decisions import (
    SigningDecisionRecordIssuer,
    VerifyOnlyDecisionRecordIssuer,
)


def _make_signing_issuer():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    priv = rsa.generate_private_key(65537, 2048, backend=default_backend())
    return SigningDecisionRecordIssuer(priv), priv.public_key()


def _make_verify_issuer(pub):
    return VerifyOnlyDecisionRecordIssuer(pub)


def _make_session_with_pending(task_id, proposal):
    """Create a mock session with a pending proposal in decisions."""
    session = MagicMock()
    session.session_id = "sess_test"
    session.mode = "producer"
    session.checkpoint.decisions = {
        "pending_decisions": {task_id: proposal},
        "decision_records": [],
        "authorized_decisions": [],
    }
    return session


# ------------------------------------------------------------------#
# DecisionToolHandler tests
# ------------------------------------------------------------------#

class TestProposeAdjudicate:
    def test_writes_proposal_returns_instruction(self):
        from snodo.mcp.decision_handlers import DecisionToolHandler

        handler = DecisionToolHandler()
        session = _make_session_with_pending("t_xyz", {})
        mgr = MagicMock()
        handler._get_active_session = MagicMock(return_value=(session, mgr))

        result = handler.handle_propose_adjudicate({
            "task_id": "t_new",
            "validator_id": "security",
            "decision": "proceed",
            "justification": "Low risk for MVP",
        })

        assert result["status"] == "pending"
        assert "snodo authorize" in result["instruction"]
        assert result["proposal"]["type"] == "adjudicate"
        assert result["proposal"]["validator_id"] == "security"

        # Verify session was updated
        mgr.update_decision.assert_called_once()
        call_args = mgr.update_decision.call_args[0]
        pending = call_args[2]
        assert "t_new" in pending
        assert pending["t_new"]["decision"] == "proceed"

    def test_missing_task_id_raises(self):
        from snodo.mcp.decision_handlers import DecisionToolHandler
        from snodo.mcp.server import MCPError

        handler = DecisionToolHandler()
        with pytest.raises(MCPError, match="task_id"):
            handler.handle_propose_adjudicate({})

    def test_invalid_decision_raises(self):
        from snodo.mcp.decision_handlers import DecisionToolHandler
        from snodo.mcp.server import MCPError

        handler = DecisionToolHandler()
        session = _make_session_with_pending("t1", {})
        mgr = MagicMock()
        handler._get_active_session = MagicMock(return_value=(session, mgr))

        with pytest.raises(MCPError, match="proceed"):
            handler.handle_propose_adjudicate({
                "task_id": "t1", "validator_id": "v1",
                "decision": "invalid", "justification": "x",
            })


class TestProposeSetModel:
    def test_writes_proposal_returns_instruction(self):
        from snodo.mcp.decision_handlers import DecisionToolHandler

        handler = DecisionToolHandler()
        session = _make_session_with_pending("t_xyz", {})
        mgr = MagicMock()
        handler._get_active_session = MagicMock(return_value=(session, mgr))

        result = handler.handle_propose_set_model({
            "task_id": "t_new",
            "proposed_model": "gemini/gemini-2.0-flash-exp",
            "scope": "coder",
            "justification": "Better cost/performance",
        })

        assert result["status"] == "pending"
        assert result["proposal"]["type"] == "set_model"
        assert result["proposal"]["proposed_model"] == "gemini/gemini-2.0-flash-exp"
        assert result["proposal"]["scope"] == "coder"


# ------------------------------------------------------------------#
# Authorize command tests
# ------------------------------------------------------------------#

class TestAuthorizeCommand:
    def test_authorize_no_pending_decision_errors(self, capsys):
        """No pending decision → clear error."""
        from snodo.cli.commands.authorize_cmd import authorize_command

        session = _make_session_with_pending("t_abc", {})

        with patch("snodo.cli.commands.authorize_cmd.SessionManager") as MockSM:
            MockSM.return_value.get_active_session.return_value = session
            with patch("snodo.cli.commands.authorize_cmd.read_state") as mock_rs:
                mock_rs.return_value = MagicMock(current_mode="producer")
                result = authorize_command(SimpleNamespace(task_id="t_nonex", yes=False))

        assert result == 1
        captured = capsys.readouterr()
        assert "No pending decision" in captured.err

    def test_authorize_adjudicate_mints_and_clears(self, capsys):
        """Adjudicate proposal → mints RS256 record, consumes proposal."""
        from snodo.cli.commands.authorize_cmd import authorize_command

        proposal = {
            "type": "adjudicate",
            "validator_id": "security",
            "decision": "proceed",
            "justification": "Low risk",
            "proposed_by": "agent",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        session = _make_session_with_pending("t_auth", proposal)
        session_mgr = MagicMock()
        session_mgr.get_active_session.return_value = session

        issuer, pub = _make_signing_issuer()

        with patch("snodo.cli.commands.authorize_cmd.SessionManager", return_value=session_mgr):
            with patch("snodo.cli.commands.authorize_cmd.read_state") as mock_rs:
                mock_rs.return_value = MagicMock(current_mode="producer")
                with patch("snodo.infrastructure.decisions.signing_issuer", return_value=issuer):
                    result = authorize_command(SimpleNamespace(task_id="t_auth", yes=True))

        assert result == 0
        captured = capsys.readouterr()
        assert "authorized" in captured.out.lower()

        # Verify proposal was consumed (pending_decisions cleared)
        call_args_list = session_mgr.update_decision.call_args_list
        # Last update_decision call should clear pending_decisions
        last_call = [c for c in call_args_list if c[0][1] == "pending_decisions"]
        assert len(last_call) > 0

        # Verify the minted record verifies
        decision_records_calls = [
            c for c in call_args_list if c[0][1] == "decision_records"
        ]
        assert len(decision_records_calls) > 0
        records = decision_records_calls[-1][0][2]
        assert len(records) >= 1

        verifier = _make_verify_issuer(pub)
        payload = verifier.verify_record(records[-1], expected_task_ref="t_auth")
        assert payload is not None
        assert payload["decision"] == "proceed"

    def test_authorize_set_model_mints_and_stores(self, capsys):
        """set_model proposal → mints RS256, stores in authorized_decisions."""
        from snodo.cli.commands.authorize_cmd import authorize_command

        proposal = {
            "type": "set_model",
            "proposed_model": "gemini/gemini-2.0-flash-exp",
            "scope": "coder",
            "justification": "Better performance",
            "proposed_by": "agent",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        session = _make_session_with_pending("t_model", proposal)
        session_mgr = MagicMock()
        session_mgr.get_active_session.return_value = session

        issuer, pub = _make_signing_issuer()

        with patch("snodo.cli.commands.authorize_cmd.SessionManager", return_value=session_mgr):
            with patch("snodo.cli.commands.authorize_cmd.read_state") as mock_rs:
                mock_rs.return_value = MagicMock(current_mode="producer")
                with patch("snodo.infrastructure.decisions.signing_issuer", return_value=issuer):
                    result = authorize_command(SimpleNamespace(task_id="t_model", yes=True))

        assert result == 0

        # Verify stored in authorized_decisions
        auth_calls = [
            c for c in session_mgr.update_decision.call_args_list
            if c[0][1] == "authorized_decisions"
        ]
        assert len(auth_calls) > 0

    def test_authorize_ignores_extra_args(self, capsys):
        """Only task_id is honored — decision content never from args."""
        from snodo.cli.commands.authorize_cmd import authorize_command

        proposal = {
            "type": "adjudicate",
            "validator_id": "architecture",
            "decision": "halt",
            "justification": "Too risky from stored state",
            "proposed_by": "agent",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        session = _make_session_with_pending("t_stored", proposal)
        issuer, pub = _make_signing_issuer()
        session_mgr = MagicMock()
        session_mgr.get_active_session.return_value = session

        # Pass extra args that should be IGNORED
        args = SimpleNamespace(task_id="t_stored", yes=True,
                                validator_id="FAKE", decision="proceed",
                                justification="OVERRIDE ATTEMPT")

        with patch("snodo.cli.commands.authorize_cmd.SessionManager", return_value=session_mgr):
            with patch("snodo.cli.commands.authorize_cmd.read_state") as mock_rs:
                mock_rs.return_value = MagicMock(current_mode="producer")
                with patch("snodo.infrastructure.decisions.signing_issuer", return_value=issuer):
                    result = authorize_command(args)

        assert result == 0

        # The record should contain the STORED decision ("halt"), not the fake one
        decision_calls = [
            c for c in session_mgr.update_decision.call_args_list
            if c[0][1] == "decision_records"
        ]
        records = decision_calls[-1][0][2]
        verifier = _make_verify_issuer(pub)
        payload = verifier.verify_record(records[-1], expected_task_ref="t_stored")
        assert payload["decision"] == "halt"  # from stored proposal, not args
