"""Tests for CLI adjudicate command (HI-CTRL mechanism).

FILE: tests/cli/test_adjudicate_cmd.py

Tests the human-only DecisionRecord minting path:
- snodo adjudicate <session> <task> <validator_id> --decision proceed --justification "..."
- mints a DecisionRecord, persists to session, audits the event
- rejects invalid decision values
- rejects adjudicated_severity=blocker (INV3)
- --decision halt also works
"""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


from snodo.infrastructure.decisions import SigningDecisionRecordIssuer


def _make_test_signing_issuer() -> SigningDecisionRecordIssuer:
    """Create a signing issuer with a throwaway test keypair."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    private = rsa.generate_private_key(65537, 2048, backend=default_backend())
    return SigningDecisionRecordIssuer(private)


TEST_SECRET = "test-secret-key-that-is-at-least-32-bytes!!"


def _make_session_manager_with_session(session_id: str, decisions: dict = None):
    """Create a SessionManager with a pre-built session."""
    from snodo.infrastructure.session import SessionManager, SessionState, Checkpoint
    from datetime import datetime, timezone

    sessions_dir = Path(tempfile.mkdtemp())
    mgr = SessionManager(sessions_dir=sessions_dir)

    session = SessionState(
        session_id=session_id,
        mode="producer",
        project_root=str(Path.cwd()),
        project_id="test",
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        checkpoint=Checkpoint(decisions=decisions or {}),
    )
    mgr._save_session(session)
    return mgr, sessions_dir


class TestAdjudicateCommand:
    """Tests for adjudicate_command()."""

    def test_mints_decision_record_proceed(self):
        """snodo adjudicate --decision proceed mints and persists a DecisionRecord."""
        session_id = "sess_test_001"
        task_id = "task_001"
        validator_id = "security"

        decisions = {
            f"resolution_{task_id}": {
                "pending_disagreement": {
                    "validator_results": [
                        {
                            "validator_id": validator_id,
                            "severity": "warn",
                            "justification": "Missing input validation",
                        }
                    ]
                }
            }
        }
        mgr, sessions_dir = _make_session_manager_with_session(session_id, decisions)

        issuer = _make_test_signing_issuer()
        with patch("snodo.infrastructure.session.SessionManager", return_value=mgr):
            with patch("snodo.infrastructure.decisions.signing_issuer", return_value=issuer):
                args = SimpleNamespace(
                    session_id=session_id,
                    task_id=task_id,
                    validator_id=validator_id,
                    decision="proceed",
                    justification="Acceptable risk for MVP",
                    resolved_by="human",
                )
                from snodo.cli.commands.adjudicate_cmd import adjudicate_command
                result = adjudicate_command(args)

        assert result == 0

        # Verify DecisionRecord was persisted
        session = mgr.load_session(session_id)
        records = session.checkpoint.decisions.get("decision_records", [])
        assert len(records) == 1

        # Verify the record is valid (same issuer)
        payload = issuer.verify_record(records[0], expected_task_ref=task_id)
        assert payload is not None
        assert payload["validator_id"] == validator_id
        assert payload["decision"] == "proceed"

    def test_mints_decision_record_halt(self):
        """snodo adjudicate --decision halt also works."""
        session_id = "sess_test_002"
        task_id = "task_002"
        validator_id = "architecture"

        decisions = {
            f"resolution_{task_id}": {
                "pending_disagreement": {
                    "validator_results": [
                        {
                            "validator_id": validator_id,
                            "severity": "warn",
                            "justification": "Design concern",
                        }
                    ]
                }
            }
        }
        mgr, sessions_dir = _make_session_manager_with_session(session_id, decisions)

        issuer = _make_test_signing_issuer()
        with patch("snodo.infrastructure.session.SessionManager", return_value=mgr):
            with patch("snodo.infrastructure.decisions.signing_issuer", return_value=issuer):
                args = SimpleNamespace(
                    session_id=session_id,
                    task_id=task_id,
                    validator_id=validator_id,
                    decision="halt",
                    justification="Too risky",
                    resolved_by="human",
                )
                from snodo.cli.commands.adjudicate_cmd import adjudicate_command
                result = adjudicate_command(args)

        assert result == 0

        session = mgr.load_session(session_id)
        records = session.checkpoint.decisions.get("decision_records", [])
        assert len(records) == 1

        payload = issuer.verify_record(records[0], expected_task_ref=task_id)
        assert payload["decision"] == "halt"

    def test_rejects_invalid_decision(self, capsys):
        """Invalid decision value is rejected."""
        args = SimpleNamespace(
            session_id="sess_x",
            task_id="task_x",
            validator_id="security",
            decision="approve",
            justification="bad",
            resolved_by="human",
        )
        from snodo.cli.commands.adjudicate_cmd import adjudicate_command
        result = adjudicate_command(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "must be" in captured.err.lower()

    def test_rejects_missing_args(self, capsys):
        """Missing required args returns error."""
        args = SimpleNamespace(
            session_id="",
            task_id="task_x",
            validator_id="security",
            decision="proceed",
            justification="ok",
            resolved_by="human",
        )
        from snodo.cli.commands.adjudicate_cmd import adjudicate_command
        result = adjudicate_command(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "required" in captured.err.lower()

    def test_rejects_missing_justification(self, capsys):
        """Missing justification returns error."""
        args = SimpleNamespace(
            session_id="sess_x",
            task_id="task_x",
            validator_id="security",
            decision="proceed",
            justification="",
            resolved_by="human",
        )
        from snodo.cli.commands.adjudicate_cmd import adjudicate_command
        result = adjudicate_command(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "required" in captured.err.lower()

    def test_session_not_found(self, capsys):
        """Non-existent session returns error."""
        args = SimpleNamespace(
            session_id="sess_nonexistent",
            task_id="task_x",
            validator_id="security",
            decision="proceed",
            justification="ok",
            resolved_by="human",
        )
        from snodo.cli.commands.adjudicate_cmd import adjudicate_command
        result = adjudicate_command(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "not found" in captured.err.lower()

    def test_audits_decision_record_issued(self):
        """Adjudication produces a decision_record_issued audit event."""
        session_id = "sess_test_003"
        task_id = "task_003"
        validator_id = "security"

        decisions = {
            f"resolution_{task_id}": {
                "pending_disagreement": {
                    "validator_results": [
                        {
                            "validator_id": validator_id,
                            "severity": "warn",
                            "justification": "Missing input validation",
                        }
                    ]
                }
            }
        }
        mgr, sessions_dir = _make_session_manager_with_session(session_id, decisions)

        audit_log = MagicMock()
        with patch("snodo.infrastructure.session.SessionManager", return_value=mgr):
            with patch("snodo.infrastructure.decisions.signing_issuer") as mock_signing:
                mock_issuer = MagicMock()
                mock_record = MagicMock()
                mock_record.jwt = "test.jwt.payload"
                mock_record.adjudicated_severity = "warn"
                mock_issuer.issue_record.return_value = mock_record
                mock_issuer._record_id.return_value = "abc123"
                mock_signing.return_value = mock_issuer

                args = SimpleNamespace(
                    session_id=session_id,
                    task_id=task_id,
                    validator_id=validator_id,
                    decision="proceed",
                    justification="OK",
                    resolved_by="human",
                )
                from snodo.cli.commands.adjudicate_cmd import adjudicate_command
                result = adjudicate_command(args)

        assert result == 0
        # Verify issue_record was called with correct args
        call_kwargs = mock_issuer.issue_record.call_args[1]
        assert call_kwargs["task_ref"] == task_id
        assert call_kwargs["validator_id"] == validator_id
        assert call_kwargs["decision"] == "proceed"


class TestAdjudicateINV3:
    """INV3: blocker severity cannot be adjudicated."""

    def test_rejects_blocker_severity(self, capsys):
        """Adjudicating a blocker is rejected (INV3)."""
        session_id = "sess_test_004"
        task_id = "task_004"
        validator_id = "security"

        # Session has a blocker result
        decisions = {
            f"resolution_{task_id}": {
                "pending_disagreement": {
                    "validator_results": [
                        {
                            "validator_id": validator_id,
                            "severity": "blocker",
                            "justification": "SQL injection vulnerability",
                        }
                    ]
                }
            }
        }
        mgr, sessions_dir = _make_session_manager_with_session(session_id, decisions)

        with patch("snodo.infrastructure.session.SessionManager", return_value=mgr):
            with patch("snodo.infrastructure.decisions.signing_issuer", return_value=_make_test_signing_issuer()):
                args = SimpleNamespace(
                    session_id=session_id,
                    task_id=task_id,
                    validator_id=validator_id,
                    decision="proceed",
                    justification="Accept the risk",
                    resolved_by="human",
                )
                from snodo.cli.commands.adjudicate_cmd import adjudicate_command
                result = adjudicate_command(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "blocker" in captured.err.lower()

    def test_rejects_error_severity(self, capsys):
        """Adjudicating an error is rejected (INV3)."""
        session_id = "sess_test_005"
        task_id = "task_005"
        validator_id = "llm_security"

        decisions = {
            f"resolution_{task_id}": {
                "pending_disagreement": {
                    "validator_results": [
                        {
                            "validator_id": validator_id,
                            "severity": "error",
                            "justification": "LLM failed to produce verdict",
                        }
                    ]
                }
            }
        }
        mgr, sessions_dir = _make_session_manager_with_session(session_id, decisions)

        with patch("snodo.infrastructure.session.SessionManager", return_value=mgr):
            with patch("snodo.infrastructure.decisions.signing_issuer", return_value=_make_test_signing_issuer()):
                args = SimpleNamespace(
                    session_id=session_id,
                    task_id=task_id,
                    validator_id=validator_id,
                    decision="proceed",
                    justification="Retry later",
                    resolved_by="human",
                )
                from snodo.cli.commands.adjudicate_cmd import adjudicate_command
                result = adjudicate_command(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "error" in captured.err.lower()


class TestAdjudicateFallback:
    """When validator result not found in session, fallback to minimal result."""

    def test_fallback_warn_result(self):
        """If no validator result found in session, creates a minimal warn result."""
        session_id = "sess_test_006"
        task_id = "task_006"
        validator_id = "security"

        # No pending_disagreement or validation_results
        decisions = {}
        mgr, sessions_dir = _make_session_manager_with_session(session_id, decisions)

        issuer = _make_test_signing_issuer()
        with patch("snodo.infrastructure.session.SessionManager", return_value=mgr):
            with patch("snodo.infrastructure.decisions.signing_issuer", return_value=issuer):
                args = SimpleNamespace(
                    session_id=session_id,
                    task_id=task_id,
                    validator_id=validator_id,
                    decision="proceed",
                    justification="OK",
                    resolved_by="human",
                )
                from snodo.cli.commands.adjudicate_cmd import adjudicate_command
                result = adjudicate_command(args)

        assert result == 0

        session = mgr.load_session(session_id)
        records = session.checkpoint.decisions.get("decision_records", [])
        assert len(records) == 1

        payload = issuer.verify_record(records[0], expected_task_ref=task_id)
        assert payload is not None
        assert payload["adjudicated_severity"] == "warn"


class TestResolveCommandDelegation:
    """Tests for resolve_cmd.py backward-compat wrapper delegating to adjudicate."""

    def test_resolve_with_validator_id_delegates(self):
        """snodo resolve <session> <task> <validator_id> delegates to adjudicate."""
        session_id = "sess_test_007"
        task_id = "task_007"
        validator_id = "security"

        decisions = {
            f"resolution_{task_id}": {
                "pending_disagreement": {
                    "validator_results": [
                        {
                            "validator_id": validator_id,
                            "severity": "warn",
                            "justification": "Concern",
                        }
                    ]
                }
            }
        }
        mgr, sessions_dir = _make_session_manager_with_session(session_id, decisions)

        with patch("snodo.infrastructure.session.SessionManager", return_value=mgr):
            with patch("snodo.infrastructure.decisions.signing_issuer", return_value=_make_test_signing_issuer()):
                args = SimpleNamespace(
                    session_id=session_id,
                    task_id=task_id,
                    validator_id=validator_id,
                    decision="proceed",
                    justification="OK",
                    resolved_by="cli",
                )
                from snodo.cli.commands.resolve_cmd import resolve_command
                result = resolve_command(args)

        assert result == 0
        session = mgr.load_session(session_id)
        records = session.checkpoint.decisions.get("decision_records", [])
        assert len(records) == 1

    def test_resolve_without_validator_id_finds_single(self, capsys):
        """snodo resolve <session> <task> with single escalated validator auto-adjudicates."""
        session_id = "sess_test_008"
        task_id = "task_008"
        validator_id = "security"

        decisions = {
            f"resolution_{task_id}": {
                "pending_disagreement": {
                    "validator_results": [
                        {
                            "validator_id": validator_id,
                            "severity": "warn",
                            "justification": "Concern",
                        }
                    ]
                }
            }
        }
        mgr, sessions_dir = _make_session_manager_with_session(session_id, decisions)

        with patch("snodo.infrastructure.session.SessionManager", return_value=mgr):
            with patch("snodo.infrastructure.decisions.signing_issuer", return_value=_make_test_signing_issuer()):
                args = SimpleNamespace(
                    session_id=session_id,
                    task_id=task_id,
                    validator_id=None,
                    decision="proceed",
                    justification="OK",
                    resolved_by="cli",
                )
                from snodo.cli.commands.resolve_cmd import resolve_command
                result = resolve_command(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "Adjudicating single escalated validator" in captured.out

    def test_resolve_without_validator_id_multiple_requires_choice(self, capsys):
        """Multiple escalated validators → list them and require explicit choice."""
        session_id = "sess_test_009"
        task_id = "task_009"

        decisions = {
            f"resolution_{task_id}": {
                "pending_disagreement": {
                    "validator_results": [
                        {
                            "validator_id": "security",
                            "severity": "warn",
                            "justification": "Concern A",
                        },
                        {
                            "validator_id": "architecture",
                            "severity": "warn",
                            "justification": "Concern B",
                        },
                    ]
                }
            }
        }
        mgr, sessions_dir = _make_session_manager_with_session(session_id, decisions)

        with patch("snodo.infrastructure.session.SessionManager", return_value=mgr):
            args = SimpleNamespace(
                session_id=session_id,
                task_id=task_id,
                validator_id=None,
                decision="proceed",
                justification="OK",
                resolved_by="cli",
            )
            from snodo.cli.commands.resolve_cmd import resolve_command
            result = resolve_command(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "Multiple validators escalated" in captured.out
        assert "snodo adjudicate" in captured.out

    def test_resolve_no_escalated_validators(self, capsys):
        """No escalated validators → error."""
        session_id = "sess_test_010"
        task_id = "task_010"

        decisions = {}
        mgr, sessions_dir = _make_session_manager_with_session(session_id, decisions)

        with patch("snodo.infrastructure.session.SessionManager", return_value=mgr):
            args = SimpleNamespace(
                session_id=session_id,
                task_id=task_id,
                validator_id=None,
                decision="proceed",
                justification="OK",
                resolved_by="cli",
            )
            from snodo.cli.commands.resolve_cmd import resolve_command
            result = resolve_command(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "No escalated validators" in captured.err

    def test_resolve_invalid_decision(self, capsys):
        """Invalid decision value is rejected."""
        args = SimpleNamespace(
            session_id="sess_x",
            task_id="task_x",
            validator_id="security",
            decision="approve",
            justification="bad",
            resolved_by="cli",
        )
        from snodo.cli.commands.resolve_cmd import resolve_command
        result = resolve_command(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "must be" in captured.err.lower()
