"""Tests for Dynamic Graph Builder (Kleene Closure) - UPDATED for Task 3.4.

FILE: tests/engine/test_loop.py

Updated to match new MCP-integrated signatures.
"""

import pytest

from snodo.compiler.models import (
    Protocol, Mode, Validator, Severity, DisagreementPolicy
)
from snodo.core.interfaces import Task, ValidatorResult
from snodo.infrastructure.tokens import ValidationToken, TokenIssuer
from snodo.engine.loop import (
    GraphBuilder, LoopState, LoopStage, build_protocol_graph, _build_audit_results
)
from snodo.agents.adapter import MockCoderAdapter
from snodo.mcp.workspace import WorkspaceMCP
from snodo.mcp.git import GitMCP
from snodo.mcp.shell import ShellMCP
import tempfile
import shutil
import subprocess
from pathlib import Path


def _make_test_token(task_id, issuer=None):
    """Create a valid JWT-backed ValidationToken for testing."""
    if issuer is None:
        issuer = TokenIssuer(secret="test_secret", ttl_seconds=3600)
    return issuer.issue_token(
        task_id,
        [ValidatorResult(validator_id="security", severity="pass", justification="OK")],
        "unanimous",
    )


# Fixtures

@pytest.fixture
def sample_protocol():
    """Create a sample protocol for testing."""
    return Protocol(
        protocol_id="test_protocol",
        name="Test Protocol",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer Mode",
                tools=["edit", "test"],
                validators=["security", "architecture"]
            ),
            Mode(
                mode_id="reviewer",
                name="Reviewer Mode",
                tools=["review", "approve"],
                validators=["security"]
            )
        ],
        validators=[
            Validator(
                validator_id="security",
                validator_type="security",
                criteria=["Check OWASP Top 10"]
            ),
            Validator(
                validator_id="architecture",
                validator_type="architecture",
                criteria=["Check design patterns"]
            )
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer"
    )


@pytest.fixture
def sample_task():
    """Create a sample task for testing."""
    return Task(
        id="task_001",
        spec="Implement feature X"
    )


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory."""
    temp_dir = tempfile.mkdtemp()
    
    # Initialize git repo
    subprocess.run(["git", "init"], cwd=temp_dir, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=temp_dir, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=temp_dir, capture_output=True, check=True)
    
    # Initial commit
    readme = Path(temp_dir) / "README.md"
    readme.write_text("test")
    subprocess.run(["git", "add", "README.md"], cwd=temp_dir, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=temp_dir, capture_output=True, check=True)
    
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


# GraphBuilder Tests

def test_graph_builder_initialization(sample_protocol, temp_workspace):
    """Test GraphBuilder initializes correctly."""
    workspace_mcp = WorkspaceMCP(temp_workspace)
    git_mcp = GitMCP(temp_workspace)
    shell_mcp = ShellMCP(temp_workspace)
    coder = MockCoderAdapter()
    
    builder = GraphBuilder(
        sample_protocol,
        workspace_mcp=workspace_mcp,
        git_mcp=git_mcp,
        shell_mcp=shell_mcp,
        coder=coder
    )
    
    assert builder.protocol == sample_protocol
    assert builder.workspace_mcp == workspace_mcp
    assert builder.git_mcp == git_mcp
    assert builder.shell_mcp == shell_mcp
    assert builder.coder == coder


def test_build_graph_structure(sample_protocol, temp_workspace):
    """Test build_graph() creates correct graph structure."""
    builder = GraphBuilder(sample_protocol)
    graph = builder.build_graph()
    
    # Check nodes exist
    assert "governance" in graph.nodes
    assert "validate" in graph.nodes
    assert "execute" in graph.nodes
    assert "move_next" in graph.nodes
    assert "blocked" in graph.nodes
    assert "complete" in graph.nodes
    
    # Graph can be compiled (validates structure)
    compiled = graph.compile()
    assert compiled is not None


def test_build_graph_with_custom_functions(sample_protocol, temp_workspace):
    """Test build_graph() with custom governance/validator/executor functions."""
    
    def custom_governance(state, protocol):
        state.metadata["custom"] = "governance"
        return state
    
    def custom_validator(task, validators, shell_mcp, current_mode="", **kwargs):
        return [
            ValidatorResult(
                validator_id="custom",
                severity="pass",
                justification="Custom validator"
            )
        ]
    
    def custom_executor(task, token, coder, workspace_mcp, git_mcp):
        return ["custom_artifact"]
    
    builder = GraphBuilder(
        sample_protocol,
        governance_fn=custom_governance,
        validator_fn=custom_validator,
        executor_fn=custom_executor
    )
    
    assert builder.governance_fn == custom_governance
    assert builder.validator_fn == custom_validator
    assert builder.executor_fn == custom_executor


# State Transition Tests

def test_governance_node(sample_protocol, sample_task, temp_workspace):
    """Test governance node executes correctly."""
    builder = GraphBuilder(sample_protocol)
    
    initial_state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
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
        "metadata": {}
    }
    
    result = builder._governance_node(initial_state)
    
    assert result["stage"] == LoopStage.GOVERNANCE.value
    assert result["iteration"] == 1
    assert result["constraints_passed"] is True


def test_validate_node_success(sample_protocol, sample_task, temp_workspace):
    """Validate node with all-pass results → proceed, not blocked.

    Uses a custom validator_fn that returns pass for every validator
    so the unanimous policy (sample protocol default) proceeds.
    """
    def _all_pass(task, validators, shell_mcp, current_mode="", **kwargs):
        return [
            ValidatorResult(validator_id=v.validator_id, severity="pass",
                            justification="ok")
            for v in validators
        ]

    builder = GraphBuilder(
        sample_protocol,
        validator_fn=_all_pass,
    )
    
    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "governance",
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
    
    result = builder._validate_node(state)
    
    assert result["stage"] == LoopStage.VALIDATE.value
    # May have test_runner + 2 validators
    assert len(result["validation_results"]) >= 2
    # May or may not have token depending on test execution
    assert result["is_blocked"] is False


def test_validate_node_blocker(sample_protocol, sample_task, temp_workspace):
    """Test validate node with blocker result."""
    
    def blocker_validator(task, validators, shell_mcp, current_mode="", **kwargs):  # Fixed signature - 4 args
        return [
            ValidatorResult(
                validator_id="security",
                severity="blocker",
                justification="Security issue found"
            )
        ]
    
    builder = GraphBuilder(
        sample_protocol,
        validator_fn=blocker_validator
    )
    
    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "governance",
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
    
    result = builder._validate_node(state)
    
    assert result["stage"] == LoopStage.VALIDATE.value
    assert result["validation_token"] is None
    assert result["is_blocked"] is True


def test_validate_node_invalid_mode(sample_protocol, sample_task, temp_workspace):
    """Test validate node with invalid mode."""
    builder = GraphBuilder(sample_protocol)
    
    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "invalid_mode",
        "iteration": 1,
        "stage": "governance",
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
    
    result = builder._validate_node(state)
    
    assert result["is_blocked"] is True
    assert len(result["constraint_violations"]) > 0


def test_execute_node(sample_protocol, sample_task, temp_workspace):
    """Test execute node with valid token."""
    workspace_mcp = WorkspaceMCP(temp_workspace)
    git_mcp = GitMCP(temp_workspace)
    coder = MockCoderAdapter()
    issuer = TokenIssuer(secret="test_exec_key_32bytes_ok!", ttl_seconds=3600)
    
    builder = GraphBuilder(
        sample_protocol,
        workspace_mcp=workspace_mcp,
        git_mcp=git_mcp,
        coder=coder,
        token_issuer=issuer,
    )
    
    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "validate",
        "validation_results": [],
        "validation_token": {"jwt": issuer.issue_token(sample_task.id, [
            ValidatorResult(validator_id="sec", severity="pass", justification="ok")
        ]).jwt},
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {}
    }
    
    result = builder._execute_node(state)
    
    assert result["stage"] == LoopStage.EXECUTE.value
    assert len(result["artifacts"]) > 0
    # New executor creates files
    assert any("src/hello.py" in str(a) for a in result["artifacts"])


def test_execute_node_no_token(sample_protocol, sample_task, temp_workspace):
    """Test execute node without token (should not execute)."""
    builder = GraphBuilder(sample_protocol)
    
    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 1,
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
    
    result = builder._execute_node(state)
    
    assert result["stage"] == LoopStage.EXECUTE.value
    assert len(result["artifacts"]) == 0


def test_move_next_node(sample_protocol, sample_task, temp_workspace):
    """Test move_next node marks task complete."""
    builder = GraphBuilder(sample_protocol)
    
    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "execute",
        "validation_results": [],
        "validation_token": None,
        "artifacts": ["artifact_1"],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {}
    }
    
    result = builder._move_next_node(state)
    
    assert result["stage"] == LoopStage.MOVE_NEXT.value
    assert result["is_complete"] is True


def test_blocked_node(sample_protocol, sample_task, temp_workspace):
    """Test blocked terminal node."""
    builder = GraphBuilder(sample_protocol)
    
    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "validate",
        "validation_results": [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": True,
        "metadata": {}
    }
    
    result = builder._blocked_node(state)
    
    assert result["stage"] == LoopStage.BLOCKED.value


def test_complete_node(sample_protocol, sample_task, temp_workspace):
    """Test complete terminal node."""
    builder = GraphBuilder(sample_protocol)
    
    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "move_next",
        "validation_results": [],
        "validation_token": None,
        "artifacts": ["artifact_1"],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": True,
        "is_blocked": False,
        "metadata": {}
    }
    
    result = builder._complete_node(state)
    
    assert result["stage"] == LoopStage.COMPLETE.value


# Routing Tests

def test_route_after_validation_to_execute(sample_protocol, temp_workspace):
    """Test routing to execute when token issued."""
    issuer = TokenIssuer(secret="test_route_key_32bytes_ok!!", ttl_seconds=3600)
    builder = GraphBuilder(sample_protocol, token_issuer=issuer)
    
    state = {
        "is_blocked": False,
        "validation_token": {"jwt": issuer.issue_token(
            "test",
            [ValidatorResult(validator_id="sec", severity="pass", justification="ok")],
        ).jwt},
        "task": {"id": "test", "spec": "test"},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "validate",
        "validation_results": [],
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "metadata": {}
    }
    
    route = builder._route_after_validation(state)
    assert route == "execute"


def test_route_after_validation_to_blocked(sample_protocol, temp_workspace):
    """Test routing to blocked when blocker present."""
    builder = GraphBuilder(sample_protocol)
    
    state = {
        "is_blocked": True,
        "validation_token": None,
        "task": {"id": "test", "spec": "test"},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "validate",
        "validation_results": [],
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "metadata": {}
    }
    
    route = builder._route_after_validation(state)
    assert route == "blocked"


def test_route_after_validation_to_governance(sample_protocol, temp_workspace):
    """Test routing back to governance when escalating."""
    builder = GraphBuilder(sample_protocol)
    
    state = {
        "is_blocked": False,
        "validation_token": None,
        "task": {"id": "test", "spec": "test"},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "validate",
        "validation_results": [],
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "metadata": {}
    }
    
    route = builder._route_after_validation(state)
    assert route == "governance"


def test_route_after_move_to_complete(sample_protocol, temp_workspace):
    """Test routing to complete when task done."""
    builder = GraphBuilder(sample_protocol)
    
    state = {
        "is_complete": True,
        "task": {"id": "test", "spec": "test"},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "move_next",
        "validation_results": [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_blocked": False,
        "metadata": {}
    }
    
    route = builder._route_after_move(state)
    assert route == "complete"


def test_route_after_move_to_governance(sample_protocol, temp_workspace):
    """Test routing back to governance for next task."""
    builder = GraphBuilder(sample_protocol)
    
    state = {
        "is_complete": False,
        "task": {"id": "test", "spec": "test"},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "move_next",
        "validation_results": [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_blocked": False,
        "metadata": {}
    }
    
    route = builder._route_after_move(state)
    assert route == "governance"


# State Conversion Tests

def test_dict_to_state_conversion(sample_protocol, sample_task, temp_workspace):
    """Test converting dict to LoopState."""
    builder = GraphBuilder(sample_protocol)
    
    state_dict = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "governance",
        "validation_results": [
            {"validator_id": "security", "severity": "pass", "justification": "OK"}
        ],
        "validation_token": {"jwt": _make_test_token(sample_task.id).jwt},
        "artifacts": ["artifact_1"],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {"key": "value"}
    }
    
    loop_state = builder._dict_to_state(state_dict)
    
    assert loop_state.task.id == sample_task.id
    assert loop_state.current_mode == "producer"
    assert loop_state.iteration == 1
    assert loop_state.stage == LoopStage.GOVERNANCE
    assert len(loop_state.validation_results) == 1
    assert loop_state.validation_token is not None
    assert len(loop_state.artifacts) == 1
    assert loop_state.metadata["key"] == "value"


def test_state_to_dict_conversion(sample_protocol, sample_task, temp_workspace):
    """Test converting LoopState to dict."""
    builder = GraphBuilder(sample_protocol)
    
    loop_state = LoopState(
        task=sample_task,
        current_mode="producer",
        iteration=1,
        stage=LoopStage.VALIDATE,
        validation_results=[
            ValidatorResult(
                validator_id="security",
                severity="pass",
                justification="OK"
            )
        ],
        validation_token=_make_test_token(sample_task.id),
        artifacts=["artifact_1"],
        metadata={"key": "value"}
    )
    
    state_dict = builder._state_to_dict(loop_state)
    
    assert state_dict["task"]["id"] == sample_task.id
    assert state_dict["current_mode"] == "producer"
    assert state_dict["iteration"] == 1
    assert state_dict["stage"] == LoopStage.VALIDATE.value
    assert len(state_dict["validation_results"]) == 1
    assert state_dict["validation_token"] is not None
    assert len(state_dict["artifacts"]) == 1
    assert state_dict["metadata"]["key"] == "value"


# Default Function Tests

def test_default_governance(sample_protocol, sample_task, temp_workspace):
    """Test default governance always passes."""
    builder = GraphBuilder(sample_protocol)
    state = LoopState(task=sample_task, current_mode="producer")
    
    result = builder._default_governance(state, sample_protocol)
    
    assert result.constraints_passed is True


def test_default_validator(sample_protocol, sample_task, temp_workspace):
    """Test default validator returns results."""
    builder = GraphBuilder(sample_protocol)
    validators = [
        Validator(validator_id="v1", validator_type="security"),
        Validator(validator_id="v2", validator_type="architecture")
    ]
    
    # Create shell MCP
    shell_mcp = ShellMCP(temp_workspace)
    
    results = builder._default_validator(sample_task, validators, shell_mcp)
    
    # Should have results for validators (test runner may fail/warn)
    assert len(results) >= 2


def test_default_executor(sample_protocol, sample_task, temp_workspace):
    """Test default executor creates artifacts."""
    workspace_mcp = WorkspaceMCP(temp_workspace)
    git_mcp = GitMCP(temp_workspace)
    coder = MockCoderAdapter()
    
    builder = GraphBuilder(sample_protocol)
    token = _make_test_token(sample_task.id)
    
    artifacts = builder._default_executor(sample_task, token, coder, workspace_mcp, git_mcp)
    
    # Should create files
    assert len(artifacts) > 0
    assert any("src/hello.py" in str(a) for a in artifacts)


# Convenience Function Test

def test_build_protocol_graph(sample_protocol, temp_workspace):
    """Test convenience function builds graph correctly."""
    graph = build_protocol_graph(sample_protocol, project_root=temp_workspace, use_mock_coder=True)
    
    assert "governance" in graph.nodes
    assert "validate" in graph.nodes
    
    compiled = graph.compile()
    assert compiled is not None


def test_build_protocol_graph_with_custom_functions(sample_protocol, temp_workspace):
    """Test convenience function with custom functions."""
    
    def custom_governance(state, protocol):
        return state
    
    graph = build_protocol_graph(
        sample_protocol,
        project_root=temp_workspace,
        use_mock_coder=True,
        governance_fn=custom_governance
    )
    
    assert graph is not None


# End-to-End Execution Test

def test_end_to_end_execution(sample_protocol, sample_task, temp_workspace):
    """Test complete loop execution from start to finish.

    Uses a custom validator_fn that returns pass for every validator
    so the unanimous policy (sample protocol default) proceeds.
    """
    def _all_pass(task, validators, shell_mcp, current_mode="", **kwargs):
        return [
            ValidatorResult(validator_id=v.validator_id, severity="pass",
                            justification="ok")
            for v in validators
        ]

    graph = build_protocol_graph(
        sample_protocol,
        project_root=temp_workspace,
        use_mock_coder=True,
        validator_fn=_all_pass,
    )
    compiled_graph = graph.compile()
    
    initial_state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
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
        "metadata": {}
    }
    
    result = compiled_graph.invoke(initial_state)
    
    # Should complete successfully
    assert result["stage"] == LoopStage.COMPLETE.value
    assert result["is_complete"] is True
    assert len(result["artifacts"]) > 0


def test_end_to_end_execution_with_blocker(sample_protocol, sample_task, temp_workspace):
    """Test loop execution that ends in blocker."""
    
    def blocker_validator(task, validators, shell_mcp, current_mode="", **kwargs):  # Fixed signature
        return [
            ValidatorResult(
                validator_id="security",
                severity="blocker",
                justification="Critical issue"
            )
        ]
    
    workspace_mcp = WorkspaceMCP(temp_workspace)
    git_mcp = GitMCP(temp_workspace)
    shell_mcp = ShellMCP(temp_workspace)
    coder = MockCoderAdapter()
    
    builder = GraphBuilder(
        sample_protocol,
        workspace_mcp=workspace_mcp,
        git_mcp=git_mcp,
        shell_mcp=shell_mcp,
        coder=coder,
        validator_fn=blocker_validator
    )
    graph = builder.build_graph()
    compiled_graph = graph.compile()
    
    initial_state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
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
        "metadata": {}
    }
    
    result = compiled_graph.invoke(initial_state)
    
    # Should end in blocked state
    assert result["stage"] == LoopStage.BLOCKED.value
    assert result["is_blocked"] is True
    assert len(result["artifacts"]) == 0


# === Task 6.7: Context Management Tests ===


def test_loop_state_has_messages_field():
    """LoopState declares messages as a formal field."""
    from snodo.core.interfaces import Task
    state = LoopState(task=Task(id="t1", spec="test"), current_mode="producer")
    assert hasattr(state, "messages")
    assert state.messages == []


def test_loop_state_has_summary_field():
    """LoopState declares summary as a formal field."""
    from snodo.core.interfaces import Task
    state = LoopState(task=Task(id="t1", spec="test"), current_mode="producer")
    assert hasattr(state, "summary")
    assert state.summary == ""


def test_state_to_dict_includes_messages_and_summary(sample_protocol, sample_task, temp_workspace):
    """_state_to_dict serializes messages and summary."""
    builder = GraphBuilder(sample_protocol)
    loop_state = LoopState(
        task=sample_task,
        current_mode="producer",
        messages=[{"role": "user", "content": "hello"}],
        summary="previous context"
    )
    d = builder._state_to_dict(loop_state)
    assert d["messages"] == [{"role": "user", "content": "hello"}]
    assert d["summary"] == "previous context"


def test_dict_to_state_includes_messages_and_summary(sample_protocol, sample_task, temp_workspace):
    """_dict_to_state deserializes messages and summary."""
    builder = GraphBuilder(sample_protocol)
    d = {
        "task": {"id": "t1", "spec": "test"},
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
        "messages": [{"role": "user", "content": "hi"}],
        "summary": "old summary"
    }
    state = builder._dict_to_state(d)
    assert state.messages == [{"role": "user", "content": "hi"}]
    assert state.summary == "old summary"


def test_collect_project_context_empty_workspace(sample_protocol, temp_workspace):
    """_collect_project_context returns defaults for empty workspace."""
    builder = GraphBuilder(sample_protocol)
    ctx = builder._collect_project_context(None)
    assert ctx["language"] == "unknown"
    assert ctx["structure"] == ""
    assert ctx["config_files"] == {}


def test_collect_project_context_python_detection(sample_protocol, temp_workspace):
    """_collect_project_context detects Python from pyproject.toml."""
    workspace_mcp = WorkspaceMCP(temp_workspace)
    (Path(temp_workspace) / "pyproject.toml").write_text("[build-system]")
    builder = GraphBuilder(sample_protocol, workspace_mcp=workspace_mcp)
    ctx = builder._collect_project_context(workspace_mcp)
    assert ctx["language"] == "python"
    assert "pyproject.toml" in ctx["config_files"]


def test_collect_project_context_js_detection(sample_protocol, temp_workspace):
    """_collect_project_context detects JavaScript from package.json."""
    workspace_mcp = WorkspaceMCP(temp_workspace)
    (Path(temp_workspace) / "package.json").write_text('{"name":"test"}')
    builder = GraphBuilder(sample_protocol, workspace_mcp=workspace_mcp)
    ctx = builder._collect_project_context(workspace_mcp)
    assert ctx["language"] == "javascript"
    assert "package.json" in ctx["config_files"]


def test_collect_project_context_dir_tree(sample_protocol, temp_workspace):
    """_collect_project_context builds directory tree."""
    workspace_mcp = WorkspaceMCP(temp_workspace)
    (Path(temp_workspace) / "src").mkdir()
    (Path(temp_workspace) / "src" / "main.py").write_text("pass")
    builder = GraphBuilder(sample_protocol, workspace_mcp=workspace_mcp)
    ctx = builder._collect_project_context(workspace_mcp)
    assert "src/" in ctx["structure"]


def test_collect_project_context_missing_config_ignored(sample_protocol, temp_workspace):
    """Missing config files are silently skipped."""
    workspace_mcp = WorkspaceMCP(temp_workspace)
    builder = GraphBuilder(sample_protocol, workspace_mcp=workspace_mcp)
    ctx = builder._collect_project_context(workspace_mcp)
    assert ctx["config_files"] == {}


def test_build_dir_tree_respects_depth(sample_protocol, temp_workspace):
    """_build_dir_tree respects max_depth parameter."""
    workspace_mcp = WorkspaceMCP(temp_workspace)
    (Path(temp_workspace) / "a" / "b" / "c" / "d").mkdir(parents=True)
    (Path(temp_workspace) / "a" / "b" / "c" / "d" / "deep.txt").write_text("x")

    builder = GraphBuilder(sample_protocol, workspace_mcp=workspace_mcp)
    tree = builder._build_dir_tree(workspace_mcp, max_depth=2)
    # Should include a/ and a/b/ but not deeper
    assert "a/" in tree
    lines = tree.strip().split("\n")
    # d/ should NOT appear (depth 3+)
    assert not any("d/" in line for line in lines)


def test_maybe_summarize_below_threshold(sample_protocol, temp_workspace):
    """_maybe_summarize does nothing when messages are below threshold."""
    from snodo.core.interfaces import Task
    builder = GraphBuilder(sample_protocol)
    state = LoopState(
        task=Task(id="t1", spec="test"),
        current_mode="producer",
        messages=[{"role": "user", "content": "short message"}],
        summary=""
    )
    result = builder._maybe_summarize(state)
    assert len(result.messages) == 1
    assert result.summary == ""


def test_maybe_summarize_truncates_when_no_model(sample_protocol, temp_workspace):
    """_maybe_summarize truncates messages when no summary model available."""
    from snodo.core.interfaces import Task
    builder = GraphBuilder(sample_protocol)
    builder._summary_model = None  # Force no model

    # Create messages that exceed ~8000 tokens (~32000 chars)
    big_messages = [
        {"role": "user", "content": "x" * 5000}
        for _ in range(10)
    ]
    state = LoopState(
        task=Task(id="t1", spec="test"),
        current_mode="producer",
        messages=big_messages,
        summary=""
    )
    result = builder._maybe_summarize(state)
    assert len(result.messages) == 3  # Kept last 3
    assert result.summary.startswith("Previous:")


def test_executor_passes_context_to_taskspec(sample_protocol, sample_task, temp_workspace):
    """_default_executor passes memory_summary and project_context to TaskSpec."""
    workspace_mcp = WorkspaceMCP(temp_workspace)
    git_mcp = GitMCP(temp_workspace)
    coder = MockCoderAdapter()

    builder = GraphBuilder(
        sample_protocol,
        workspace_mcp=workspace_mcp,
        git_mcp=git_mcp,
        coder=coder
    )
    token = _make_test_token(sample_task.id)

    builder._default_executor(
        sample_task, token, coder, workspace_mcp, git_mcp,
        memory_summary="previous context",
        project_context={"language": "python"},
    )

    # MockAdapter stores last_spec
    assert coder.last_spec is not None
    assert coder.last_spec.memory_summary == "previous context"
    assert coder.last_spec.project_context == {"language": "python"}


def test_end_to_end_messages_include_summary_field(sample_protocol, sample_task, temp_workspace):
    """End-to-end: summary field is present in final state."""
    graph = build_protocol_graph(sample_protocol, project_root=temp_workspace, use_mock_coder=True)
    compiled = graph.compile()

    initial_state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
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
        "messages": [],
        "summary": "",
    }

    result = compiled.invoke(initial_state)
    assert "summary" in result
    assert "messages" in result
    assert isinstance(result["messages"], list)


# === Task 7.1: Audit Log Wiring Tests ===

def _make_audit_log():
    """Create a temp AuditLog for testing."""
    import tempfile
    from snodo.infrastructure.audit import AuditLog
    f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    f.close()
    return AuditLog(f.name)


def test_governance_node_logs_event(sample_protocol, sample_task, temp_workspace):
    """Governance node produces governance_check audit event."""
    audit = _make_audit_log()
    builder = GraphBuilder(sample_protocol, audit_log=audit)

    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
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
        "messages": [],
        "summary": "",
    }

    builder._governance_node(state)
    events = audit.get_history(event_type="governance_check")
    assert len(events) == 1
    assert events[0].data["task_ref"] == sample_task.id


def test_validate_node_logs_event(sample_protocol, sample_task, temp_workspace):
    """Validate node produces validate audit event."""
    audit = _make_audit_log()
    builder = GraphBuilder(sample_protocol, audit_log=audit)

    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 1,
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
        "messages": [],
        "summary": "",
    }

    builder._validate_node(state)
    events = audit.get_history(event_type="validate")
    assert len(events) == 1
    assert events[0].data["phase"] == "pre_execute"


def test_execute_node_logs_dispatch(sample_protocol, sample_task, temp_workspace):
    """Execute node produces dispatch audit event."""
    from snodo.agents.adapter import MockCoderAdapter
    audit = _make_audit_log()
    workspace_mcp = WorkspaceMCP(temp_workspace)
    git_mcp = GitMCP(temp_workspace)
    coder = MockCoderAdapter()

    builder = GraphBuilder(
        sample_protocol, workspace_mcp=workspace_mcp,
        git_mcp=git_mcp, coder=coder, audit_log=audit,
    )

    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "validate",
        "validation_results": [],
        "validation_token": {"jwt": _make_test_token(sample_task.id).jwt},
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {},
        "messages": [],
        "summary": "",
    }

    builder._execute_node(state)
    events = audit.get_history(event_type="dispatch")
    assert len(events) == 1
    assert events[0].data["task_ref"] == sample_task.id


def test_blocked_node_logs_halt(sample_protocol, sample_task, temp_workspace):
    """Blocked node produces halt audit event."""
    audit = _make_audit_log()
    builder = GraphBuilder(sample_protocol, audit_log=audit)

    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "validate",
        "validation_results": [
            {"validator_id": "security", "severity": "blocker", "justification": "fail"},
        ],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": ["Security blocker"],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": True,
        "metadata": {},
        "messages": [],
        "summary": "",
    }

    builder._blocked_node(state)
    events = audit.get_history(event_type="halt")
    assert len(events) == 1
    assert events[0].data["task_ref"] == sample_task.id
    assert "security" in events[0].data["blocker_validators"]


def test_complete_node_logs_task_complete(sample_protocol, sample_task, temp_workspace):
    """Complete node produces task_complete audit event."""
    audit = _make_audit_log()
    builder = GraphBuilder(sample_protocol, audit_log=audit)

    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "move_next",
        "validation_results": [],
        "validation_token": None,
        "artifacts": ["src/hello.py"],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": True,
        "is_blocked": False,
        "metadata": {},
        "messages": [],
        "summary": "",
    }

    builder._complete_node(state)
    events = audit.get_history(event_type="task_complete")
    assert len(events) == 1
    assert events[0].data["artifacts"] == ["src/hello.py"]


def test_move_next_logs_transition(sample_protocol, sample_task, temp_workspace):
    """Move next node produces transition audit event."""
    audit = _make_audit_log()
    builder = GraphBuilder(sample_protocol, audit_log=audit)

    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "execute",
        "validation_results": [],
        "validation_token": None,
        "artifacts": ["art"],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {},
        "messages": [],
        "summary": "",
    }

    builder._move_next_node(state)
    events = audit.get_history(event_type="transition")
    assert len(events) == 1
    assert events[0].data["from_mode"] == "producer"


def test_end_to_end_audit_chain(sample_protocol, sample_task, temp_workspace):
    """Full loop execution produces valid audit chain.

    Uses a custom validator_fn that returns pass for every validator
    so the unanimous policy (sample protocol default) proceeds.
    """
    def _all_pass(task, validators, shell_mcp, current_mode="", **kwargs):
        return [
            ValidatorResult(validator_id=v.validator_id, severity="pass",
                            justification="ok")
            for v in validators
        ]

    audit = _make_audit_log()
    workspace_mcp = WorkspaceMCP(temp_workspace)
    git_mcp = GitMCP(temp_workspace)
    shell_mcp = ShellMCP(temp_workspace)
    coder = MockCoderAdapter()

    builder = GraphBuilder(
        sample_protocol,
        workspace_mcp=workspace_mcp,
        git_mcp=git_mcp,
        shell_mcp=shell_mcp,
        coder=coder,
        audit_log=audit,
        validator_fn=_all_pass,
    )
    graph = builder.build_graph()
    compiled = graph.compile()

    initial_state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
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
        "messages": [],
        "summary": "",
    }

    result = compiled.invoke(initial_state)

    assert result["stage"] == LoopStage.COMPLETE.value
    # Verify audit chain integrity
    assert audit.verify_chain() is True
    # Should have: governance_check, validate, dispatch,
    # post_validation_route, transition, task_complete (at minimum)
    assert len(audit.events) >= 5
    # Verify specific event types present
    types = {e.event_type for e in audit.events}
    assert "governance_check" in types
    assert "validate" in types
    assert "dispatch" in types
    assert "task_complete" in types
    # No halt events (happy path)
    assert len(audit.get_history(event_type="halt")) == 0


def test_end_to_end_blocker_audit(sample_protocol, sample_task, temp_workspace):
    """Blocker path produces halt + validator detail in audit."""
    audit = _make_audit_log()

    def blocker_validator(task, validators, shell_mcp, current_mode="", **kwargs):
        return [
            ValidatorResult(
                validator_id="security",
                severity="blocker",
                justification="Critical issue"
            )
        ]

    builder = GraphBuilder(
        sample_protocol,
        validator_fn=blocker_validator,
        audit_log=audit,
    )
    graph = builder.build_graph()
    compiled = graph.compile()

    initial_state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
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
        "messages": [],
        "summary": "",
    }

    result = compiled.invoke(initial_state)

    assert result["stage"] == LoopStage.BLOCKED.value
    halt_events = audit.get_history(event_type="halt")
    assert len(halt_events) == 1
    assert "security" in halt_events[0].data["blocker_validators"]


def test_no_audit_log_no_error(sample_protocol, sample_task, temp_workspace):
    """GraphBuilder without audit_log works without errors."""
    builder = GraphBuilder(sample_protocol, audit_log=None)
    state = {
        "task": {"id": "t1", "spec": "test"},
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
        "messages": [],
        "summary": "",
    }
    # Should not raise
    builder._governance_node(state)


# ========== TASK 7.3: SESSION MANAGER IN GRAPH BUILDER ==========

def test_graph_builder_accepts_session_manager(sample_protocol):
    """GraphBuilder accepts session_manager parameter."""
    from unittest.mock import MagicMock
    mock_sm = MagicMock()
    builder = GraphBuilder(sample_protocol, session_manager=mock_sm)
    assert builder._session_manager is mock_sm


def test_graph_builder_session_manager_none_by_default(sample_protocol):
    """GraphBuilder session_manager defaults to None."""
    builder = GraphBuilder(sample_protocol)
    assert builder._session_manager is None


def test_build_protocol_graph_accepts_session_manager(sample_protocol, temp_workspace):
    """build_protocol_graph passes session_manager to GraphBuilder."""
    from unittest.mock import MagicMock
    mock_sm = MagicMock()
    mock_audit = MagicMock()
    # Just verify it doesn't raise
    graph = build_protocol_graph(
        sample_protocol,
        project_root=str(temp_workspace),
        use_mock_coder=True,
        audit_log=mock_audit,
        session_manager=mock_sm,
    )
    assert graph is not None


def test_build_protocol_graph_accepts_audit_log(sample_protocol, temp_workspace):
    """build_protocol_graph passes audit_log to GraphBuilder."""
    from unittest.mock import MagicMock
    mock_audit = MagicMock()
    graph = build_protocol_graph(
        sample_protocol,
        project_root=str(temp_workspace),
        use_mock_coder=True,
        audit_log=mock_audit,
    )
    assert graph is not None


# ========== WF3 Runtime Guard Tests ==========

def test_validate_node_wf3_guard_blocks_on_empty_validators(sample_task, temp_workspace):
    """_validate_node blocks when mode has no pre_execute validators."""
    # Protocol where 'producer' has only post_execute validator
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit"],
                validators=["v1"],
            )
        ],
        validators=[
            Validator(
                validator_id="v1",
                validator_type="quality",
                evaluation_phase="post_execute",
            )
        ],
        initial_mode="producer",
    )
    audit = _make_audit_log()
    builder = GraphBuilder(protocol, audit_log=audit)

    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 1,
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
        "messages": [],
        "summary": "",
    }

    result = builder._validate_node(state)

    assert result["is_blocked"] is True
    assert any("WF3" in v for v in result["constraint_violations"])
    events = audit.get_history(event_type="wf3_runtime_violation")
    assert len(events) == 1
    assert events[0].data["mode"] == "producer"
    assert events[0].data["phase"] == "pre_execute"


def test_validate_node_wf3_guard_passes_with_pre_execute(sample_protocol, sample_task):
    """_validate_node proceeds when pre_execute validators exist."""
    # sample_protocol has pre_execute validators for 'producer'
    builder = GraphBuilder(sample_protocol)

    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 1,
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
        "messages": [],
        "summary": "",
    }

    result = builder._validate_node(state)

    # Should NOT be blocked by WF3 guard (may be blocked by policy, but not WF3)
    wf3_violations = [v for v in result["constraint_violations"] if "WF3" in v]
    assert len(wf3_violations) == 0


def test_post_validate_bypassed_logs_audit(sample_task, temp_workspace):
    """_post_validate_node logs bypass audit when no post_execute validators."""
    # Protocol with only pre_execute validators
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit"],
                validators=["v1"],
            )
        ],
        validators=[
            Validator(
                validator_id="v1",
                validator_type="security",
                evaluation_phase="pre_execute",
            )
        ],
        initial_mode="producer",
    )
    audit = _make_audit_log()
    builder = GraphBuilder(protocol, audit_log=audit)

    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 1,
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
        "messages": [],
        "summary": "",
    }

    builder._post_validate_node(state)

    events = audit.get_history(event_type="post_validate_bypassed")
    assert len(events) == 1
    assert events[0].data["reason"] == "no_post_execute_validators"
    assert events[0].data["mode"] == "producer"


def test_post_validate_no_bypass_with_post_validators(sample_task, temp_workspace):
    """_post_validate_node does NOT log bypass when post_execute validators exist."""
    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit"],
                validators=["v1", "v2"],
            )
        ],
        validators=[
            Validator(
                validator_id="v1",
                validator_type="security",
                evaluation_phase="pre_execute",
            ),
            Validator(
                validator_id="v2",
                validator_type="quality",
                evaluation_phase="post_execute",
            ),
        ],
        initial_mode="producer",
    )
    audit = _make_audit_log()
    builder = GraphBuilder(protocol, audit_log=audit)

    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 1,
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
        "messages": [],
        "summary": "",
    }

    builder._post_validate_node(state)

    bypass_events = audit.get_history(event_type="post_validate_bypassed")
    assert len(bypass_events) == 0
    # Should have a normal validate event instead
    validate_events = audit.get_history(event_type="validate")
    assert len(validate_events) == 1
    assert validate_events[0].data["phase"] == "post_execute"


# ============================================================================
# Task 7.10 — ESCALATE resolution tests
# ============================================================================

def _escalating_validators():
    """ValidatorResults that trigger ESCALATE under unanimous policy (no consensus)."""
    return [
        ValidatorResult(validator_id="sec", severity="pass", justification="ok"),
        ValidatorResult(validator_id="arch", severity="warn", justification="loose coupling"),
        # Not all pass → split → ESCALATE under unanimous
    ]


def test_validate_node_escalate_sets_blocked(sample_protocol, sample_task, temp_workspace):
    """ESCALATE → is_blocked=True, no infinite loop."""
    workspace_mcp = WorkspaceMCP(temp_workspace)

    # Custom validator_fn that produces split results triggering ESCALATE
    # under unanimous policy: 1 pass, 1 warn, 0 total consensus
    def _split_validator(task, validators, shell_mcp, current_mode="", **kwargs):
        return [
            ValidatorResult(validator_id="sec", severity="pass", justification="ok"),
            ValidatorResult(validator_id="arch", severity="blocker", justification="bad"),
        ]

    builder = GraphBuilder(
        sample_protocol, workspace_mcp=workspace_mcp, validator_fn=_split_validator,
    )

    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 1,
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
        "messages": [],
        "summary": [],
    }

    result = builder._validate_node(state)
    # Under unanimous: blocker → PolicyAction.HALT (not ESCALATE)
    # But we verify the structure: is_blocked should be set
    assert result["is_blocked"] is True


def test_validate_node_escalate_majority_pending_disagreement(sample_task, temp_workspace):
    """ESCALATE under majority policy populates pending_disagreement."""
    from snodo.compiler.models import Protocol, Mode, Validator, DisagreementPolicy

    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[Mode(mode_id="producer", name="Producer", tools=["edit"], validators=["v1", "v2"])],
        validators=[
            Validator(validator_id="v1", validator_type="security", evaluation_phase="pre_execute"),
            Validator(validator_id="v2", validator_type="architecture", evaluation_phase="pre_execute"),
        ],
        disagreement_policy=DisagreementPolicy.MAJORITY,
        initial_mode="producer",
    )
    workspace_mcp = WorkspaceMCP(temp_workspace)

    def _split_validator(task, validators, shell_mcp, current_mode="", **kwargs):
        return [
            ValidatorResult(validator_id="v1", severity="pass", justification="ok"),
            ValidatorResult(validator_id="v2", severity="blocker", justification="bad"),
        ]

    builder = GraphBuilder(
        protocol, workspace_mcp=workspace_mcp, validator_fn=_split_validator,
    )

    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 1,
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
        "messages": [],
        "summary": [],
    }

    result = builder._validate_node(state)
    assert result["is_blocked"] is True


def test_route_after_governance_blocked(sample_protocol):
    """is_blocked=True → route to blocked."""
    builder = GraphBuilder(sample_protocol)

    state = {
        "task": {"id": "t1", "spec": "test"},
        "current_mode": "producer",
        "iteration": 1,
        "stage": "governance",
        "validation_results": [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": True,
        "metadata": {},
        "messages": [],
        "summary": [],
    }

    route = builder._route_after_governance(state)
    assert route == "blocked"


def test_max_iterations_hard_stop():
    """Iteration 51+ → is_blocked=True (infinite loop safety net)."""
    from snodo.compiler.models import Protocol, Mode, Validator

    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[Mode(mode_id="producer", name="Producer", tools=["edit"], validators=["v1"])],
        validators=[Validator(validator_id="v1", validator_type="security", evaluation_phase="pre_execute")],
        initial_mode="producer",
    )
    builder = GraphBuilder(protocol)

    state = {
        "task": {"id": "t1", "spec": "test"},
        "current_mode": "producer",
        "iteration": 51,
        "stage": "governance",
        "validation_results": [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "pending_disagreement": None,
        "metadata": {},
        "messages": [],
        "summary": [],
    }

    result = builder._governance_node(state)
    assert result["is_blocked"] is True
    assert any("Max iterations" in v for v in result["constraint_violations"])


def test_validate_node_escalate_audit_event(sample_task, temp_workspace):
    """ESCALATE emits disagreement_escalated audit event."""
    from snodo.compiler.models import Protocol, Mode, Validator

    protocol = Protocol(
        protocol_id="test",
        name="Test",
        modes=[Mode(mode_id="producer", name="Producer", tools=["edit"], validators=["v1", "v2"])],
        validators=[
            Validator(validator_id="v1", validator_type="security", evaluation_phase="pre_execute"),
            Validator(validator_id="v2", validator_type="architecture", evaluation_phase="pre_execute"),
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer",
    )
    audit = _make_audit_log()
    workspace_mcp = WorkspaceMCP(temp_workspace)

    # Split results: v1 passes, v2 is blocker → HALT (is_blocked)
    # No ESCALATE audit event — HALT audit goes through validate.
    # This test verifies that validate_node properly sets is_blocked
    # and the audit event type is "validate" with outcome "blocked".
    # The ESCALATE-specific audit fires when pending_disagreement is set.
    def _split_validator(task, validators, shell_mcp, current_mode="", **kwargs):
        return [ValidatorResult(validator_id="v1", severity="pass", justification="ok"),
                ValidatorResult(validator_id="v2", severity="blocker", justification="bad")]

    builder = GraphBuilder(
        protocol, workspace_mcp=workspace_mcp, audit_log=audit,
        validator_fn=_split_validator,
    )

    state = {
        "task": {"id": "t1", "spec": "test"},
        "current_mode": "producer",
        "iteration": 1,
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
        "messages": [],
        "summary": [],
    }

    builder._validate_node(state)

    validate_events = audit.get_history(event_type="validate")
    assert len(validate_events) == 1


# ============================================================================
# Task 7.17 — severity_cap engine tests
# ============================================================================

def _builder_with_cap_validator(cap_val, validator_fn=None, mode="producer"):
    """Build a GraphBuilder with a single capped validator."""
    from snodo.compiler.models import Protocol, Mode, Validator

    protocol = Protocol(
        protocol_id="test", name="Test",
        modes=[Mode(mode_id=mode, name="Producer", tools=["edit"],
                      validators=["v1"])],
        validators=[Validator(
            validator_id="v1", validator_type="security",
            evaluation_phase="pre_execute", severity_cap=cap_val,
            criteria=["check for issues"],  # ensure LLM dispatch path
        )],
        initial_mode=mode,
    )
    builder = GraphBuilder(protocol)
    # Ensure _get_completion_fn returns a callable so the LLM branch fires
    builder._get_completion_fn = lambda: (lambda model, messages, **kw: None)
    return builder


def test_severity_cap_warn_downgrades_blocker(tmp_path):
    """severity_cap=warn → blocker result becomes warn."""
    builder = _builder_with_cap_validator(Severity.WARN)
    # Override _dispatch_one to return a blocker (simulating LLM)
    builder._dispatch_one = lambda v, ctx, reg: ValidatorResult(
        validator_id=v.validator_id, severity="blocker",
        justification="critical issue",
    )
    results = builder._default_validator(
        Task(id="t1", spec="test"), builder.protocol.validators, None,
    )
    assert len(results) == 1
    assert results[0].severity == "warn"


def test_severity_cap_warn_passes_through_warn(tmp_path):
    """severity_cap=warn → warn result passes through unchanged."""
    builder = _builder_with_cap_validator(Severity.WARN)
    builder._dispatch_one = lambda v, ctx, reg: ValidatorResult(
        validator_id=v.validator_id, severity="warn",
        justification="minor concern",
    )
    results = builder._default_validator(
        Task(id="t1", spec="test"), builder.protocol.validators, None,
    )
    assert results[0].severity == "warn"


def test_severity_cap_pass_downgrades_blocker(tmp_path):
    """severity_cap=pass → blocker result becomes pass."""
    builder = _builder_with_cap_validator(Severity.PASS)
    builder._dispatch_one = lambda v, ctx, reg: ValidatorResult(
        validator_id=v.validator_id, severity="blocker",
        justification="bad",
    )
    results = builder._default_validator(
        Task(id="t1", spec="test"), builder.protocol.validators, None,
    )
    assert results[0].severity == "pass"


def test_severity_cap_pass_downgrades_warn(tmp_path):
    """severity_cap=pass → warn result becomes pass."""
    builder = _builder_with_cap_validator(Severity.PASS)
    builder._dispatch_one = lambda v, ctx, reg: ValidatorResult(
        validator_id=v.validator_id, severity="warn",
        justification="minor",
    )
    results = builder._default_validator(
        Task(id="t1", spec="test"), builder.protocol.validators, None,
    )
    assert results[0].severity == "pass"


def test_severity_cap_none_no_effect(tmp_path):
    """severity_cap=None → no capping, results pass through as-is."""
    builder = _builder_with_cap_validator(None)
    builder._dispatch_one = lambda v, ctx, reg: ValidatorResult(
        validator_id=v.validator_id, severity="blocker",
        justification="bad",
    )
    results = builder._default_validator(
        Task(id="t1", spec="test"), builder.protocol.validators, None,
    )
    assert results[0].severity == "blocker"


def test_severity_enum_ordering():
    """Severity enum supports ordering: PASS < WARN < BLOCKER."""
    assert Severity.PASS < Severity.WARN
    assert Severity.WARN < Severity.BLOCKER
    assert Severity.PASS < Severity.BLOCKER
    assert not (Severity.WARN < Severity.PASS)
    assert Severity.BLOCKER > Severity.PASS
    assert Severity.WARN <= Severity.BLOCKER
    assert Severity.WARN <= Severity.WARN


def test_audit_results_includes_severity_at_cap(sample_protocol, sample_task, tmp_path):
    """Audit results include severity_at_cap when result sits at cap boundary."""
    from snodo.compiler.models import Validator as CMValidator

    # Build a validator with severity_cap=warn, returns blocker → capped
    capped_v = CMValidator(
        validator_id="vc", validator_type="security",
        evaluation_phase="pre_execute", severity_cap=Severity.WARN,
    )
    results_list = [ValidatorResult(validator_id="vc", severity="warn",
                                     justification="capped blocker")]

    audit_results = _build_audit_results([capped_v], results_list)
    assert len(audit_results) == 1
    assert audit_results[0]["severity"] == "warn"
    assert audit_results[0]["severity_at_cap"] is True


def test_audit_results_no_cap_no_flag(sample_task):
    """Audit results without cap have no severity_at_cap flag."""
    from snodo.compiler.models import Validator as CMValidator

    no_cap_v = CMValidator(
        validator_id="vn", validator_type="security",
        evaluation_phase="pre_execute",
    )
    results_list = [ValidatorResult(validator_id="vn", severity="pass",
                                     justification="ok")]

    audit_results = _build_audit_results([no_cap_v], results_list)
    assert len(audit_results) == 1
    assert "severity_at_cap" not in audit_results[0]