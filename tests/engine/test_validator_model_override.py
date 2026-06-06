"""Tests for set_model override application to validators.

W5-05c-1: verified set_model records override validator models.
"""

from unittest.mock import MagicMock

from snodo.core.interfaces import Task
from snodo.engine.validators import ValidatorRunner
from snodo.compiler.models import Validator


def _make_signing_issuer():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    from snodo.infrastructure.decisions import SigningDecisionRecordIssuer
    priv = rsa.generate_private_key(65537, 2048, backend=default_backend())
    return SigningDecisionRecordIssuer(priv), priv.public_key()


def _make_verify_issuer(pub):
    from snodo.infrastructure.decisions import VerifyOnlyDecisionRecordIssuer
    return VerifyOnlyDecisionRecordIssuer(pub)


def _make_set_model_jwt(signing_issuer, scope, proposed_model, task_ref="t1"):
    import jwt
    from datetime import datetime, timezone
    payload = {
        "iat": datetime.now(timezone.utc),
        "task_ref": task_ref,
        "type": "set_model",
        "proposed_model": proposed_model,
        "scope": scope,
        "justification": "test",
        "resolved_by": "human",
    }
    return jwt.encode(payload, signing_issuer._private_key, algorithm="RS256")


def _stub_result(vid, sev="pass"):
    from snodo.core.interfaces import ValidatorResult
    return ValidatorResult(validator_id=vid, severity=sev, justification="stub")


class TestSetModelOverride:
    """Verified set_model records override validator models."""

    def test_verified_override_applies(self):
        signer, pub = _make_signing_issuer()
        verifier = _make_verify_issuer(pub)
        jwt_str = _make_set_model_jwt(signer, "validator:security", "gemini-2.0")

        runner = ValidatorRunner(
            protocol=MagicMock(), completion_fn=MagicMock(),
            default_model="claude-sonnet-4-20250514",
            validator_config=MagicMock(max_tokens=1500, max_tool_turns=6),
            audit_log=None, workspace_mcp=None, git_mcp=None, session_manager=None,
        )
        dispatched = []
        runner._dispatch_one = lambda v, ctx, reg: dispatched.append((v.validator_id, ctx.model)) or _stub_result(v.validator_id)

        runner.run(
            Task(id="t1", spec="test"),
            [Validator(validator_id="security", validator_type="security")],
            None, current_mode="producer",
            authorized_decisions=[jwt_str], decision_issuer=verifier,
        )
        assert dispatched[0] == ("security", "gemini-2.0")

    def test_tampered_record_not_applied(self):
        signer, pub = _make_signing_issuer()
        verifier = _make_verify_issuer(pub)
        jwt_str = _make_set_model_jwt(signer, "validator:security", "gemini-2.0")
        parts = jwt_str.split(".")
        tampered = f"{parts[0]}.{parts[1] + 'X'}.{parts[2]}"

        runner = ValidatorRunner(
            protocol=MagicMock(), completion_fn=MagicMock(),
            default_model="claude-sonnet-4-20250514",
            validator_config=MagicMock(max_tokens=1500, max_tool_turns=6),
            audit_log=None, workspace_mcp=None, git_mcp=None, session_manager=None,
        )
        dispatched = []
        runner._dispatch_one = lambda v, ctx, reg: dispatched.append((v.validator_id, ctx.model)) or _stub_result(v.validator_id)

        runner.run(
            Task(id="t1", spec="test"),
            [Validator(validator_id="security", validator_type="security")],
            None, current_mode="producer",
            authorized_decisions=[tampered], decision_issuer=verifier,
        )
        assert dispatched[0] == ("security", "claude-sonnet-4-20250514")

    def test_coder_scoped_ignored(self):
        signer, pub = _make_signing_issuer()
        verifier = _make_verify_issuer(pub)
        jwt_str = _make_set_model_jwt(signer, "coder", "gpt-4o")

        runner = ValidatorRunner(
            protocol=MagicMock(), completion_fn=MagicMock(),
            default_model="claude-sonnet-4-20250514",
            validator_config=MagicMock(max_tokens=1500, max_tool_turns=6),
            audit_log=None, workspace_mcp=None, git_mcp=None, session_manager=None,
        )
        dispatched = []
        runner._dispatch_one = lambda v, ctx, reg: dispatched.append((v.validator_id, ctx.model)) or _stub_result(v.validator_id)

        runner.run(
            Task(id="t1", spec="test"),
            [Validator(validator_id="security", validator_type="security")],
            None, current_mode="producer",
            authorized_decisions=[jwt_str], decision_issuer=verifier,
        )
        assert dispatched[0] == ("security", "claude-sonnet-4-20250514")

    def test_no_set_model_cascade_unchanged(self):
        runner = ValidatorRunner(
            protocol=MagicMock(), completion_fn=MagicMock(),
            default_model="claude-sonnet-4-20250514",
            validator_config=MagicMock(max_tokens=1500, max_tool_turns=6),
            audit_log=None, workspace_mcp=None, git_mcp=None, session_manager=None,
        )
        dispatched = []
        runner._dispatch_one = lambda v, ctx, reg: dispatched.append((v.validator_id, ctx.model)) or _stub_result(v.validator_id)

        runner.run(
            Task(id="t1", spec="test"),
            [Validator(validator_id="security", validator_type="security")],
            None, current_mode="producer",
            authorized_decisions=[], decision_issuer=None,
        )
        assert dispatched[0] == ("security", "claude-sonnet-4-20250514")

    def test_precedence_override_beats_v_model(self):
        signer, pub = _make_signing_issuer()
        verifier = _make_verify_issuer(pub)
        jwt_str = _make_set_model_jwt(signer, "validator:security", "gemini-2.0")

        runner = ValidatorRunner(
            protocol=MagicMock(), completion_fn=MagicMock(),
            default_model="claude-sonnet-4-20250514",
            validator_config=MagicMock(max_tokens=1500, max_tool_turns=6),
            audit_log=None, workspace_mcp=None, git_mcp=None, session_manager=None,
        )
        dispatched = []
        runner._dispatch_one = lambda v, ctx, reg: dispatched.append((v.validator_id, ctx.model)) or _stub_result(v.validator_id)

        runner.run(
            Task(id="t1", spec="test"),
            [Validator(validator_id="security", validator_type="security", model="gpt-4")],
            None, current_mode="producer",
            authorized_decisions=[jwt_str], decision_issuer=verifier,
        )
        assert dispatched[0] == ("security", "gemini-2.0")

    def test_different_validator_not_affected(self):
        signer, pub = _make_signing_issuer()
        verifier = _make_verify_issuer(pub)
        jwt_str = _make_set_model_jwt(signer, "validator:security", "gemini-2.0")

        runner = ValidatorRunner(
            protocol=MagicMock(), completion_fn=MagicMock(),
            default_model="claude-sonnet-4-20250514",
            validator_config=MagicMock(max_tokens=1500, max_tool_turns=6),
            audit_log=None, workspace_mcp=None, git_mcp=None, session_manager=None,
        )
        dispatched = []
        runner._dispatch_one = lambda v, ctx, reg: dispatched.append((v.validator_id, ctx.model)) or _stub_result(v.validator_id)

        runner.run(
            Task(id="t1", spec="test"),
            [
                Validator(validator_id="security", validator_type="security"),
                Validator(validator_id="architecture", validator_type="architecture"),
            ],
            None, current_mode="producer",
            authorized_decisions=[jwt_str], decision_issuer=verifier,
        )
        assert dispatched[0] == ("security", "gemini-2.0")
        assert dispatched[1] == ("architecture", "claude-sonnet-4-20250514")


class TestFindSetModelOverrides:
    """Unit tests for find_set_model_overrides."""

    def test_returns_verified_set_model_payloads(self):
        signer, pub = _make_signing_issuer()
        verifier = _make_verify_issuer(pub)
        valid = _make_set_model_jwt(signer, "validator:sec", "gemini-2.0")
        tampered = _make_set_model_jwt(signer, "validator:arch", "gpt-4o")
        parts = tampered.split(".")
        tampered = f"{parts[0]}.{parts[1] + 'X'}.{parts[2]}"

        result = verifier.find_set_model_overrides([valid, tampered])
        assert len(result) == 1
        assert result[0]["proposed_model"] == "gemini-2.0"

    def test_returns_empty_for_no_set_model(self):
        _, pub = _make_signing_issuer()
        verifier = _make_verify_issuer(pub)
        assert verifier.find_set_model_overrides([]) == []

    def test_adjudicate_type_skipped(self):
        signer, pub = _make_signing_issuer()
        verifier = _make_verify_issuer(pub)
        from snodo.core.interfaces import ValidatorResult
        result = ValidatorResult(validator_id="sec", severity="warn", justification="x")
        record = signer.issue_record("t1", "sec", result, "proceed", "ok")
        overrides = verifier.find_set_model_overrides([record.jwt])
        assert overrides == []
