"""Validation node branch coverage tests.

FILE: tests/engine/test_loop_validation.py
"""

from unittest.mock import MagicMock

import pytest
from snodo.compiler.models import DisagreementPolicy, Mode, Protocol, Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.engine.loop import GraphBuilder


@pytest.fixture
def sample_protocol():
    return Protocol(
        protocol_id="test_protocol",
        name="Test Protocol",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer Mode",
                tools=["edit", "test"],
                validators=["security"]
            )
        ],
        validators=[
            Validator(
                validator_id="security",
                validator_type="security",
                criteria=["Check OWASP Top 10"]
            )
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer"
    )


@pytest.fixture
def sample_task():
    return Task(
        id="task_001",
        spec="Implement feature X"
    )


def test_validate_node_invalid_mode(sample_protocol, sample_task):
    """invalid mode -> is_blocked, halt_type="constraint\""""
    builder = GraphBuilder(sample_protocol)
    initial_state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "nonexistent_mode",
        "iteration": 0,
        "stage": "validate",
        "validation_results": [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {}
    }
    result = builder._validate_node(initial_state)
    assert result["is_blocked"] is True
    assert result["halt_type"] == "constraint"
    assert "Invalid mode:" in result["constraint_violations"][0]


def test_validate_node_wf3_empty_validators(sample_task):
    """WF3 empty pre_execute validators -> halt_type="wf3\" + audit"""
    protocol_empty_validators = Protocol(
        protocol_id="test_protocol",
        name="Test Protocol",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer Mode",
                tools=["edit", "test"],
                validators=[]
            )
        ],
        validators=[
            Validator(
                validator_id="dummy",
                validator_type="dummy",
                criteria=["Dummy"]
            )
        ],
        initial_mode="producer"
    )

    mock_audit = MagicMock()
    builder = GraphBuilder(protocol_empty_validators, audit_log=mock_audit)
    initial_state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 0,
        "stage": "validate",
        "validation_results": [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {}
    }
    result = builder._validate_node(initial_state)
    assert result["is_blocked"] is True
    assert result["halt_type"] == "wf3"
    assert "WF3 violation" in result["constraint_violations"][0]
    mock_audit.append_event.assert_any_call("wf3_runtime_violation", {
        "task_ref": "task_001",
        "mode": "producer",
        "phase": "pre_execute"
    })


def test_validate_node_escalate_spec_authoring(sample_task):
    """Warn-only ESCALATE from a spec-quality validator routes to spec authoring."""
    # Unanimous policy with a 'warn' result triggers ESCALATE; a judges_spec
    # validator's critique is spec-quality, so it routes to spec authoring.
    protocol = Protocol(
        protocol_id="test_protocol",
        name="Test Protocol",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer Mode",
                tools=["edit", "test"],
                validators=["security"]
            )
        ],
        validators=[
            Validator(
                validator_id="security",
                validator_type="security",
                criteria=["Check OWASP Top 10"],
                judges_spec=True,
            )
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer"
    )

    def mock_validator_fn(task, validators, shell_mcp, **kwargs):
        return [ValidatorResult(validator_id="security", severity="warn", justification="Warning justification")]

    mock_audit = MagicMock()
    builder = GraphBuilder(protocol, validator_fn=mock_validator_fn, audit_log=mock_audit)
    initial_state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 0,
        "stage": "validate",
        "validation_results": [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {}
    }
    result = builder._validate_node(initial_state)
    # Warn-only ESCALATE now routes to spec authoring instead of blocking
    assert result.get("is_blocked") is False
    assert result.get("needs_spec_authoring") is True
    assert result.get("halt_type") is None or result["halt_type"] != "escalated"
    assert result["pending_disagreement"] is not None
    assert result["pending_disagreement"]["phase"] == "pre_execute"
    mock_audit.append_event.assert_any_call("disagreement_escalated", {
        "op": "disagreement_escalated",
        "phase": "pre_execute",
        "task_ref": "task_001",
        "policy": "unanimous",
        "validator_results": [{"validator_id": "security", "severity": "warn", "justification": "Warning justification"}],
        "policy_decision": {
            "pass_count": 0,
            "warn_count": 1,
            "blocker_count": 0,
            "total_count": 1,
            "justification": "Unanimous policy requires all validators to pass"
        }
    })


def test_validate_node_escalate_with_blocker_still_blocks(sample_task):
    """Blocker result leads to HALT, not spec authoring."""
    protocol = Protocol(
        protocol_id="test_protocol",
        name="Test Protocol",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer Mode",
                tools=["edit", "test"],
                validators=["security"]
            )
        ],
        validators=[
            Validator(
                validator_id="security",
                validator_type="security",
                criteria=["Check OWASP Top 10"]
            )
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer"
    )

    def mock_validator_fn(task, validators, shell_mcp, **kwargs):
        return [ValidatorResult(validator_id="security", severity="blocker", justification="Blocker justification")]

    mock_audit = MagicMock()
    builder = GraphBuilder(protocol, validator_fn=mock_validator_fn, audit_log=mock_audit)
    initial_state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 0,
        "stage": "validate",
        "validation_results": [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {}
    }
    result = builder._validate_node(initial_state)
    # Blocker → HALT → is_blocked, halt_type="blocked" (not spec authoring)
    assert result.get("is_blocked") is True
    assert result.get("halt_type") == "blocked"
    assert not result.get("needs_spec_authoring", False)


def test_validate_node_halt_blocker(sample_task):
    """HALT with a blocker-severity result -> halt_type="blocked\""""
    protocol = Protocol(
        protocol_id="test_protocol",
        name="Test Protocol",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer Mode",
                tools=["edit", "test"],
                validators=["security"]
            )
        ],
        validators=[
            Validator(
                validator_id="security",
                validator_type="security",
                criteria=["Check OWASP Top 10"]
            )
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer"
    )

    def mock_validator_fn(task, validators, shell_mcp, **kwargs):
        return [ValidatorResult(validator_id="security", severity="blocker", justification="Failed")]

    builder = GraphBuilder(protocol, validator_fn=mock_validator_fn)
    initial_state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 0,
        "stage": "validate",
        "validation_results": [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {}
    }
    result = builder._validate_node(initial_state)
    assert result["is_blocked"] is True
    assert result["halt_type"] == "blocked"


def test_execute_node_execution_error(sample_protocol, sample_task):
    """_execute_node: executor_fn raises ExecutionError -> blocks & audits"""
    from snodo.core.interfaces import ExecutionError

    mock_audit = MagicMock()
    def mock_executor(task, token, coder, workspace_mcp, git_mcp, **kwargs):
        raise ExecutionError("Execution failed completely")

    builder = GraphBuilder(sample_protocol, executor_fn=mock_executor, audit_log=mock_audit)

    # Mock token verification to be True
    builder._token_issuer = MagicMock()
    builder._token_issuer.verify_token.return_value = True

    # We must also mock _collect_project_context
    builder._collect_project_context = MagicMock(return_value={})

    initial_state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 0,
        "stage": "execute",
        "validation_results": [],
        "validation_token": {"jwt": "valid_token"},
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {}
    }

    result = builder._execute_node(initial_state)
    assert result["is_blocked"] is True
    assert result["halt_type"] == "internal_error"
    assert "Execution failed completely" in result["constraint_violations"]
    # Post-validation is skipped, not passed.
    assert result["metadata"]["post_validation"]["outcome"] == "skipped"
    mock_audit.append_event.assert_any_call("execution_failed", {
        "op": "execution_failed",
        "task_ref": sample_task.id,
        "error": "Execution failed completely",
    })


def test_execute_node_success(sample_protocol, sample_task):
    """_execute_node: successful-dispatch path token verified -> artifacts appended -> token consumed/None"""
    mock_audit = MagicMock()
    def mock_executor(task, token, coder, workspace_mcp, git_mcp, **kwargs):
        return ["file1.txt"]

    builder = GraphBuilder(sample_protocol, executor_fn=mock_executor, audit_log=mock_audit)

    builder._token_issuer = MagicMock()
    builder._token_issuer.verify_token.return_value = True
    builder._collect_project_context = MagicMock(return_value={})

    initial_state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 0,
        "stage": "execute",
        "validation_results": [],
        "validation_token": {"jwt": "valid_token"},
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {}
    }

    result = builder._execute_node(initial_state)
    assert result["is_blocked"] is False
    assert result["validation_token"] is None
    assert "file1.txt" in result["artifacts"]
    mock_audit.append_event.assert_any_call("token_consumed", {
        "op": "token_consumed",
        "task_ref": sample_task.id,
    })
    mock_audit.append_event.assert_any_call("dispatch", {
        "op": "dispatch",
        "task_ref": sample_task.id,
        "token_id": sample_task.id,
        "mode": "producer",
        "coder": "mock",
        "coder_model": builder.coder.model,
        "judging_model": builder._default_model,
        "artifacts_count": 1,
    })


def test_post_validate_node_bypassed(sample_task):
    """_post_validate_node: "no post_execute validators" bypass path"""
    # Create protocol with no post_execute validators (validators empty or not matching)
    protocol = Protocol(
        protocol_id="test_protocol",
        name="Test Protocol",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer Mode",
                tools=["edit"],
                validators=[]
            )
        ],
        validators=[
            Validator(
                validator_id="dummy",
                validator_type="dummy",
                criteria=["criteria"],
                evaluation_phase="pre_execute"
            )
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer"
    )

    mock_audit = MagicMock()
    builder = GraphBuilder(protocol, audit_log=mock_audit)

    initial_state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 0,
        "stage": "validate",
        "validation_results": [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {}
    }

    result = builder._post_validate_node(initial_state)
    assert result["is_blocked"] is False
    mock_audit.append_event.assert_any_call("post_validate_bypassed", {
        "task_ref": sample_task.id,
        "mode": "producer",
        "reason": "no_post_execute_validators",
    })


def test_post_validate_node_halt(sample_task):
    """_post_validate_node: HALT path when policy decision is HALT"""
    protocol = Protocol(
        protocol_id="test_protocol",
        name="Test Protocol",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer Mode",
                tools=["edit"],
                validators=["security"]
            )
        ],
        validators=[
            Validator(
                validator_id="security",
                validator_type="security",
                criteria=["Check OWASP Top 10"],
                evaluation_phase="post_execute"
            )
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer"
    )

    def mock_validator_fn(task, validators, shell_mcp, **kwargs):
        return [ValidatorResult(validator_id="security", severity="blocker", justification="Failed post-validation")]

    builder = GraphBuilder(protocol, validator_fn=mock_validator_fn)

    initial_state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 0,
        "stage": "validate",
        "validation_results": [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {}
    }

    result = builder._post_validate_node(initial_state)
    assert result["is_blocked"] is True
    assert result["halt_type"] == "blocked"
    assert "Post-execute validation failed" in result["constraint_violations"][0]


class TestProgressOutput:
    """Node transitions are printed on the normal path; verdicts are verbose-only."""

    def _protocol(self):
        return Protocol(
            protocol_id="test_protocol",
            name="Test Protocol",
            version="1.0.0",
            modes=[
                Mode(
                    mode_id="producer",
                    name="Producer Mode",
                    tools=["edit", "test"],
                    validators=["security"],
                )
            ],
            validators=[
                Validator(
                    validator_id="security",
                    validator_type="security",
                    criteria=["Check OWASP Top 10"],
                )
            ],
            disagreement_policy=DisagreementPolicy.UNANIMOUS,
            initial_mode="producer",
        )

    def _state(self, task_id="task_001"):
        return {
            "task": {"id": task_id, "spec": "Implement feature X"},
            "current_mode": "producer",
            "iteration": 0,
            "stage": "validate",
            "validation_results": [],
            "validation_token": None,
            "artifacts": [],
            "constraints_passed": True,
            "constraint_violations": [],
            "policy_decision": None,
            "is_complete": False,
            "is_blocked": False,
            "metadata": {},
        }

    def test_validate_node_prints_transition(self, capsys):
        """Entering validation prints the validator list on the normal path."""
        def mock_validator_fn(task, validators, shell_mcp, **kwargs):
            return [ValidatorResult(validator_id="security", severity="warn",
                                    justification="warn")]

        builder = GraphBuilder(self._protocol(), validator_fn=mock_validator_fn)
        builder._validate_node(self._state())

        out = capsys.readouterr().out
        assert "Validating (pre-execute): security" in out

    def test_validate_node_verdicts_verbose_only(self, capsys):
        """Per-validator pass verdicts are printed only when verbose is set; warnings surface on normal path."""
        def mock_pass_fn(task, validators, shell_mcp, **kwargs):
            result = ValidatorResult(validator_id="security", severity="pass", justification="pass")
            cb = kwargs.get("progress_cb")
            if cb is not None:
                cb("security", result)
            return [result]

        builder = GraphBuilder(self._protocol(), validator_fn=mock_pass_fn)
        builder._validate_node(self._state())
        out = capsys.readouterr().out
        assert "security: pass" not in out

        builder_verbose = GraphBuilder(self._protocol(), validator_fn=mock_pass_fn, verbose=True)
        builder_verbose._validate_node(self._state())
        out_verbose = capsys.readouterr().out
        assert "security: pass" in out_verbose


class TestExecutionFailureReporting:
    """A failed execution is internal_error, never validated, never called a blocker."""

    def _protocol(self):
        return Protocol(
            protocol_id="exec_fail",
            name="Exec Fail",
            version="1.0.0",
            modes=[Mode(mode_id="producer", name="Producer", tools=["edit"],
                        validators=["pre", "post"])],
            validators=[
                Validator(validator_id="pre", validator_type="security",
                          evaluation_phase="pre_execute"),
                Validator(validator_id="post", validator_type="quality",
                          evaluation_phase="post_execute"),
            ],
            disagreement_policy=DisagreementPolicy.UNANIMOUS,
            initial_mode="producer",
        )

    def _builder(self, executor_fn):
        from snodo.infrastructure.tokens import TokenIssuer

        from tests.conftest import TEST_SECRET

        def passing_validator(task, validators, shell_mcp, **kwargs):
            return [ValidatorResult(validator_id=v.validator_id, severity="pass",
                                    justification="ok") for v in validators]

        return GraphBuilder(
            self._protocol(),
            validator_fn=passing_validator,
            executor_fn=executor_fn,
            token_issuer=TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600),
        )

    @staticmethod
    def _state():
        return {
            "task": {"id": "t1", "spec": "do thing"},
            "current_mode": "producer",
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
        }

    def test_execute_failure_is_internal_error_and_skips_post_validation(self):
        from snodo.core.interfaces import ExecutionError

        def failing_executor(task, token, coder, workspace_mcp, git_mcp, **kwargs):
            raise ExecutionError("coder produced nothing")

        builder = self._builder(failing_executor)
        result = builder.build_graph().compile().invoke(self._state())

        assert result["is_blocked"] is True
        assert result["halt_type"] == "internal_error"

        payload = result["metadata"]["halt_payload"]
        assert payload["raw_halt_type"] == "internal_error"
        assert payload["halt_type"] == "internal_error"
        assert payload["final_decision"] == "internal_error"
        # The failure reason reaches the top-level reason.
        assert payload["reason"] is not None
        assert "coder produced nothing" in payload["reason"]
        # Post-validation was skipped, not passed.
        assert payload["post_validation"]["outcome"] == "skipped"

    def test_completed_execution_unaffected(self):
        def succeeding_executor(task, token, coder, workspace_mcp, git_mcp, **kwargs):
            return ["src/thing.py"]

        builder = self._builder(succeeding_executor)
        result = builder.build_graph().compile().invoke(self._state())

        assert result["is_blocked"] is False
        assert "src/thing.py" in result["artifacts"]
        assert result["halt_type"] is None


