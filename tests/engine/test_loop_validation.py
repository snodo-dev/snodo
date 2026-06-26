"""Validation node branch coverage tests.

FILE: tests/engine/test_loop_validation.py
"""

import pytest
from unittest.mock import patch, MagicMock
from snodo.compiler.models import Protocol, Mode, Validator, DisagreementPolicy
from snodo.engine.loop import GraphBuilder, LoopStage
from snodo.core.interfaces import Task, ValidatorResult


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


def test_validate_node_escalate(sample_task):
    """ESCALATE -> halt_type="escalated\" + pending_disagreement + audit"""
    # Unanimous policy with a 'warn' result triggers ESCALATE
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
    assert result["is_blocked"] is True
    assert result["halt_type"] == "escalated"
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
    assert result["halt_type"] == "execution_error"
    assert "Execution failed completely" in result["constraint_violations"]
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


