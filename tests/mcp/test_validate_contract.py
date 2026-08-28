"""Four-outcome validate_task contract tests (ADR 015).

FILE: tests/mcp/test_validate_contract.py

Covers:
- unit: each of the four outcomes (pass / escalate / blocker / validator_error)
- regression: a failing test suite yields `blocker`, never `warn`
- parity: engine and MCP produce the same validator severities
- integration: escalate → authorize → re-validate → token issued
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from snodo.compiler.models import Protocol
from snodo.core.interfaces import Task, ValidatorResult
from snodo.infrastructure.session import SessionManager
from snodo.mcp.server import ProtocolMCPServer

from tests.mcp._validate_helpers import (
    validation_passing,
    warn_completion_fn,
    blocker_completion_fn,
    mock_validator_config,
)


SECURITY_PROTOCOL_DATA = {
    "protocol_id": "contract",
    "name": "Contract",
    "version": "1.0.0",
    "modes": [
        {
            "mode_id": "producer",
            "name": "Producer",
            "tools": ["edit", "dispatch"],
            "validators": ["security"],
        },
    ],
    "validators": [
        {
            "validator_id": "security",
            "validator_type": "security",
            "criteria": ["Check security"],
        },
    ],
    "disagreement_policy": "unanimous",
    "initial_mode": "producer",
}


@pytest.fixture
def protocol():
    return Protocol(**SECURITY_PROTOCOL_DATA)


@pytest.fixture
def project_dir():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q"], cwd=d, check=False)
    subprocess.run(["git", "config", "user.email", "t@t.co"], cwd=d, check=False)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=d, check=False)
    (Path(d) / "README.md").write_text("test")
    subprocess.run(["git", "add", "."], cwd=d, check=False)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=False)
    snodo_dir = Path(d) / ".snodo"
    snodo_dir.mkdir(exist_ok=True)
    (snodo_dir / "state.json").write_text(
        json.dumps({"current_mode": "producer", "active_session": {}})
    )
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def server(protocol, project_dir):
    return ProtocolMCPServer(protocol, project_dir)


def _patch_completion(fn):
    return patch(
        "snodo.validators.runner.resolve_validator_completion",
        return_value=(fn, "mock-model", mock_validator_config()),
    ), patch("snodo.validators.llm_validator.supports_response_schema", return_value=False)


# ---------------------------------------------------------------------------
# Four outcomes
# ---------------------------------------------------------------------------

class TestFourOutcomes:
    def test_pass_issues_token(self, server):
        """All validators pass → status=pass + token issued."""
        with validation_passing(server):
            result = server.call_tool("validate_task", {"task_id": "t1", "task_spec": "x"})
        assert result["status"] == "pass"
        assert result["token_issued"] is True
        assert server._validation_token is not None
        assert server._validation_status == "pass"

    def test_escalate_no_token_returns_decision(self, server):
        """Warn under unanimous → escalate, no token, decision_id + evidence."""
        c, s = _patch_completion(warn_completion_fn())
        with c, s:
            result = server.call_tool("validate_task", {"task_id": "t1", "task_spec": "x"})
        assert result["status"] == "escalate"
        assert result["token_issued"] is False
        assert result["decision_id"] == "t1"
        assert "options" in result
        assert "snodo authorize" in result["instruction"]
        assert any(
            r["validator_id"] == "security" and r["severity"] == "warn"
            for r in result["results"]
        )

    def test_blocker_no_token(self, server):
        """A blocker from a declared validator yields blocker, not warn; never a token."""
        c, s = _patch_completion(blocker_completion_fn())
        with c, s:
            result = server.call_tool("validate_task", {"task_id": "t1", "task_spec": "x"})
        assert result["status"] == "blocker"
        assert result["token_issued"] is False
        sec = [r for r in result["results"] if r["validator_id"] == "security"]
        assert sec and sec[0]["severity"] == "blocker"
        assert "authorize" not in result["instruction"]

    def test_validator_error_no_token_no_authorize(self, server):
        """A validator LLM resolution failure → validator_error, no authorize advice."""
        with patch(
            "snodo.validators.runner.resolve_validator_completion",
            side_effect=RuntimeError("config broken"),
        ):
            result = server.call_tool("validate_task", {"task_id": "t1", "task_spec": "x"})
        assert result["status"] == "validator_error"
        assert result["token_issued"] is False
        assert "authorize" not in result["instruction"]
        assert "authorize" not in result.get("instruction", "")

    def test_validator_exception_yields_validator_error(self, server):
        """A validator whose evaluate() raises → validator_error (not pass)."""
        from snodo.validators.context import ValidatorBase
        from snodo.validators.registry import _default_registry

        class _Exploding(ValidatorBase):
            def __init__(self, validator_spec):
                self.validator_spec = validator_spec

            @classmethod
            def registered_type(cls):
                return "exploding_contract_test"

            def evaluate(self, context):
                raise RuntimeError("boom")

        _default_registry.register("exploding_contract_test", _Exploding)

        protocol = Protocol(**{
            **SECURITY_PROTOCOL_DATA,
            "validators": [
                {"validator_id": "security", "validator_type": "exploding_contract_test",
                 "criteria": ["Check"]},
            ],
        })
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=d, check=False)
        (Path(d) / ".snodo").mkdir(exist_ok=True)
        srv = ProtocolMCPServer(protocol, d)
        try:
            result = srv._handle_validate_task({"task_id": "t1", "task_spec": "x"})
            assert result["status"] == "validator_error"
            assert result["token_issued"] is False
            assert "authorize" not in result["instruction"]
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Regression: failing tests → blocker, not warn
# ---------------------------------------------------------------------------

class TestFailingTestsAreBlocker:
    def test_failing_test_suite_is_blocker(self, server):
        c, s = _patch_completion(blocker_completion_fn())
        with c, s:
            result = server._handle_validate_task({"task_id": "t1", "task_spec": "x"})
        assert result["status"] == "blocker"
        # No result is a warn "Tests (continuing)" downgrade
        assert not any(
            "Tests (continuing)" in r["justification"] for r in result["results"]
        )


# ---------------------------------------------------------------------------
# Parity: engine and MCP share the same validator runner
# ---------------------------------------------------------------------------

class TestEngineMCPParity:
    def test_same_validator_same_severity(self, server, protocol, project_dir):
        """The MCP handler and the engine produce identical validator severities."""
        from snodo.validators.runner import run_validators

        # Deterministic (non-LLM) validator so no completion is needed.
        from snodo.validators.context import ValidatorBase
        from snodo.validators.registry import _default_registry

        class _FixedWarn(ValidatorBase):
            def __init__(self, validator_spec):
                self.validator_spec = validator_spec

            @classmethod
            def registered_type(cls):
                return "fixed_warn_contract_test"

            def evaluate(self, context):
                return ValidatorResult(
                    validator_id=self.validator_spec.validator_id,
                    severity="warn",
                    justification="fixed",
                )

        _default_registry.register("fixed_warn_contract_test", _FixedWarn)

        proto = Protocol(**{
            **SECURITY_PROTOCOL_DATA,
            "validators": [
                {"validator_id": "security", "validator_type": "fixed_warn_contract_test",
                 "criteria": ["Check"]},
            ],
        })

        # Engine path
        validators = proto.get_validators_by_phase("pre_execute")
        task = Task(id="t1", spec="x")
        engine_results, _ = run_validators(
            protocol=proto, validators=validators, task=task, phase="pre_execute",
            completion_fn=None, default_model="mock-model",
            validator_config=mock_validator_config(),
        )

        # MCP path
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=d, check=False)
        (Path(d) / ".snodo").mkdir(exist_ok=True)
        srv = ProtocolMCPServer(proto, d)
        try:
            mcp = srv._handle_validate_task({"task_id": "t1", "task_spec": "x"})
        finally:
            shutil.rmtree(d, ignore_errors=True)

        engine_sev = {r.validator_id: r.severity for r in engine_results}
        mcp_sev = {r["validator_id"]: r["severity"] for r in mcp["results"]}
        assert engine_sev["security"] == "warn"
        assert mcp_sev["security"] == "warn"
        assert engine_sev["security"] == mcp_sev["security"]

        # Parity: same validator set → same total_count and same policy decision.
        from snodo.engine.policy import PolicyEvaluator

        engine_decision = PolicyEvaluator().evaluate(
            engine_results, proto.disagreement_policy, "pre_execute", task_ref="t1",
        )
        mcp_decision = PolicyEvaluator().evaluate(
            [ValidatorResult(**r) for r in mcp["results"]],
            proto.disagreement_policy, "pre_execute", task_ref="t1",
        )
        assert engine_decision.total_count == mcp_decision.total_count == 1
        assert engine_decision.action == mcp_decision.action
        assert engine_decision.pass_count == mcp_decision.pass_count
        assert engine_decision.warn_count == mcp_decision.warn_count
        assert engine_decision.blocker_count == mcp_decision.blocker_count


# ---------------------------------------------------------------------------
# Integration: escalate → authorize → re-validate → token
# ---------------------------------------------------------------------------

class TestEscalateAuthorizeRevalidate:
    def _keys(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.backends import default_backend
        from snodo.infrastructure.decisions import (
            SigningDecisionRecordIssuer, VerifyOnlyDecisionRecordIssuer,
        )
        priv = rsa.generate_private_key(65537, 2048, backend=default_backend())
        return SigningDecisionRecordIssuer(priv), VerifyOnlyDecisionRecordIssuer(priv.public_key())

    def test_full_loop(self, server, project_dir):
        signing, verify = self._keys()
        server._decision_issuer = verify
        from snodo.engine.policy import PolicyEvaluator
        server._policy_evaluator = PolicyEvaluator(decision_issuer=verify)

        # Active session for decision persistence
        mgr = SessionManager()
        session = mgr.create_session("producer", project_dir)

        # 1. validate → escalate (security warns under unanimous)
        c, s = _patch_completion(warn_completion_fn())
        with c, s:
            result = server.call_tool("validate_task", {"task_id": "t1", "task_spec": "x"})
        assert result["status"] == "escalate"
        assert result["token_issued"] is False

        # pending decision persisted
        session = mgr.get_active_session("producer", project_dir)
        pending = session.checkpoint.decisions.get("pending_decisions", {})
        assert "t1" in pending
        assert pending["t1"]["validator_id"] == "security"

        # 2. human authorizes (out-of-band): mint a signed DecisionRecord
        record = signing.issue_record(
            task_ref="t1",
            validator_id="security",
            validator_result=ValidatorResult(
                validator_id="security", severity="warn", justification="concern",
            ),
            decision="proceed",
            justification="human approves",
        )
        records = session.checkpoint.decisions.get("decision_records", [])
        records.append(record.jwt)
        mgr.update_decision(session.session_id, "decision_records", records)
        # consume the pending proposal
        pending = session.checkpoint.decisions.get("pending_decisions", {})
        pending.pop("t1", None)
        mgr.update_decision(session.session_id, "pending_decisions", pending)

        # 3. re-validate → pass + token
        with c, s:
            result2 = server.call_tool("validate_task", {"task_id": "t1", "task_spec": "x"})
        assert result2["status"] == "pass"
        assert result2["token_issued"] is True
