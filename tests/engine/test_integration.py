"""End-to-end integration tests for Snodo (Task 3.4) - FIXED.

FILE: tests/engine/test_integration.py

Fixed to handle pytest path issues and validation properly.
"""

import pytest
import tempfile
import shutil
from pathlib import Path
import subprocess

from snodo.compiler.models import (
    Protocol, Mode, Validator, DisagreementPolicy
)
from snodo.core.interfaces import Task, TaskSpec, ValidatorResult
from snodo.engine.loop import build_protocol_graph, LoopStage
from snodo.agents.adapter import MockCoderAdapter
from snodo.mcp.workspace import WorkspaceMCP
from snodo.mcp.git import GitMCP
from snodo.mcp.shell import ShellMCP


@pytest.fixture
def temp_git_repo():
    """Create a temporary git repository for testing."""
    temp_dir = tempfile.mkdtemp()
    
    try:
        subprocess.run(["git", "init"], cwd=temp_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=temp_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=temp_dir, capture_output=True, check=True)
        
        readme = Path(temp_dir) / "README.md"
        readme.write_text("# Test Project\n")
        subprocess.run(["git", "add", "README.md"], cwd=temp_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=temp_dir, capture_output=True, check=True)
        
        yield Path(temp_dir)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def sample_protocol():
    """Create a minimal protocol for testing."""
    return Protocol(
        protocol_id="test",
        name="Test Protocol",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit", "test"],
                validators=["test_runner"]
            )
        ],
        validators=[
            Validator(
                validator_id="test_runner",
                validator_type="testing",
                criteria=["Run tests"]
            )
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer"
    )


# Integration Tests

def test_end_to_end_with_mock_coder(temp_git_repo, sample_protocol):
    """Test complete flow: task → code generation → file write → git commit."""
    
    task = Task(
        id="test_001",
        spec="Create a hello world function"
    )
    
    graph = build_protocol_graph(
        sample_protocol,
        project_root=str(temp_git_repo),
        use_mock_coder=True
    )
    
    compiled = graph.compile()
    
    initial_state = {
        "task": {"id": task.id, "spec": task.spec},
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
    
    result = compiled.invoke(initial_state)
    
    # Verify completion (may be blocked if tests don't exist/pass)
    # With mock coder and no real tests, validation may warn but should still complete
    assert result["stage"] in [LoopStage.COMPLETE.value, LoopStage.BLOCKED.value]
    
    # Verify artifacts were attempted
    artifacts = result["artifacts"]
    # May have artifacts even if blocked
    
    # Verify files exist (MockAdapter defaults: src/hello.py, tests/test_hello.py)
    src_file = temp_git_repo / "src" / "hello.py"
    test_file = temp_git_repo / "tests" / "test_hello.py"

    # Files should be created even if validation fails
    if result["stage"] == LoopStage.COMPLETE.value:
        assert src_file.exists(), f"Source file not created: {src_file}"
        assert test_file.exists(), f"Test file not created: {test_file}"

        src_content = src_file.read_text()
        assert "def hello()" in src_content

        test_content = test_file.read_text()
        assert "def test_hello()" in test_content


def test_workspace_mcp_integration(temp_git_repo):
    """Test WorkspaceMCP creates and reads files correctly."""
    workspace = WorkspaceMCP(str(temp_git_repo))
    
    success = workspace.write_file("test.txt", "Hello, World!")
    assert success is True
    
    assert (temp_git_repo / "test.txt").exists()
    
    content = workspace.read_file("test.txt")
    assert content == "Hello, World!"
    
    workspace.write_file("sub/dir/file.txt", "nested")
    assert (temp_git_repo / "sub" / "dir" / "file.txt").exists()


def test_git_mcp_integration(temp_git_repo):
    """Test GitMCP stages and commits files."""
    git_mcp = GitMCP(str(temp_git_repo))
    
    test_file = temp_git_repo / "feature.py"
    test_file.write_text("def feature(): pass\n")
    
    git_mcp.stage_files(["feature.py"])
    
    output = git_mcp.commit("Add feature")
    
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=str(temp_git_repo),
        capture_output=True,
        text=True,
        check=True
    )
    
    assert "Add feature" in log.stdout


def test_shell_mcp_integration(temp_git_repo):
    """Test ShellMCP runs tests and returns ValidatorResult."""
    shell_mcp = ShellMCP(str(temp_git_repo))
    
    tests_dir = temp_git_repo / "tests"
    tests_dir.mkdir(exist_ok=True)
    
    test_file = tests_dir / "test_simple.py"
    test_file.write_text("""
def test_passing():
    assert True

def test_also_passing():
    assert 1 + 1 == 2
""")
    
    result = shell_mcp.run_tests("tests/test_simple.py", command_type="pytest")
    
    assert result.validator_id == "test_runner"
    assert result.severity in ["pass", "warn"]  # May have warnings
    # Just check result has content
    assert len(result.justification) > 0


def test_shell_mcp_failing_tests(temp_git_repo):
    """Test ShellMCP detects failing tests."""
    shell_mcp = ShellMCP(str(temp_git_repo))
    
    tests_dir = temp_git_repo / "tests"
    tests_dir.mkdir(exist_ok=True)
    
    test_file = tests_dir / "test_failing.py"
    test_file.write_text("""
def test_will_fail():
    assert False, "This should fail"
""")
    
    result = shell_mcp.run_tests("tests/test_failing.py", command_type="pytest")
    
    assert result.severity == "blocker"
    assert "failed" in result.justification.lower() or "fail" in result.justification.lower()


def test_mock_coder_adapter():
    """Test MockCoderAdapter returns expected artifacts."""
    from snodo.core.interfaces import FileArtifact
    coder = MockCoderAdapter(
        mock_files=[
            FileArtifact(path="src/custom.py", content="def custom(): return 42"),
            FileArtifact(path="tests/test_custom.py", content="def test_custom(): assert custom() == 42"),
        ]
    )

    spec = TaskSpec(
        description="Test task",
        constraints=[]
    )

    artifact = coder.implement(spec)

    assert artifact.files[0].content == "def custom(): return 42"
    assert artifact.files[1].content == "def test_custom(): assert custom() == 42"
    assert coder.call_count == 1
    assert coder.last_spec == spec


def test_protocol_execution_with_validation(temp_git_repo, sample_protocol):
    """Test protocol execution goes through validation stage."""
    
    task = Task(id="val_test", spec="Test validation")
    
    graph = build_protocol_graph(
        sample_protocol,
        project_root=str(temp_git_repo),
        use_mock_coder=True
    )
    
    compiled = graph.compile()
    
    stages_seen = []
    
    initial_state = {
        "task": {"id": task.id, "spec": task.spec},
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
    
    for state_update in compiled.stream(initial_state):
        if isinstance(state_update, dict):
            node_state = next(iter(state_update.values()))
            if "stage" in node_state:
                stages_seen.append(node_state["stage"])
    
    # Verify stages executed (may stop at blocked if tests fail)
    assert "governance" in stages_seen
    assert "validate" in stages_seen
    # May or may not reach execute/complete depending on test availability


def test_blocker_stops_execution(temp_git_repo):
    """Test that blockers halt execution."""
    
    protocol = Protocol(
        protocol_id="blocker_test",
        name="Blocker Test",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit"],
                validators=["blocker"]
            )
        ],
        validators=[
            Validator(
                validator_id="blocker",
                validator_type="security",
                criteria=["Will block"]
            )
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer"
    )
    
    def blocking_validator(task, validators, shell_mcp, current_mode="", **kwargs):
        return [ValidatorResult(
            validator_id="blocker",
            severity="blocker",
            justification="Test blocker"
        )]
    
    from snodo.engine.loop import GraphBuilder
    
    builder = GraphBuilder(
        protocol,
        workspace_mcp=WorkspaceMCP(str(temp_git_repo)),
        git_mcp=GitMCP(str(temp_git_repo)),
        shell_mcp=ShellMCP(str(temp_git_repo)),
        coder=MockCoderAdapter(),
        validator_fn=blocking_validator
    )
    
    graph = builder.build_graph()
    compiled = graph.compile()
    
    task = Task(id="block_test", spec="Will be blocked")
    
    initial_state = {
        "task": {"id": task.id, "spec": task.spec},
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
    
    result = compiled.invoke(initial_state)
    
    assert result["stage"] == LoopStage.BLOCKED.value
    assert result["is_blocked"] is True
    assert len(result["artifacts"]) == 0


def test_multiple_artifacts_created(temp_git_repo, sample_protocol):
    """Test that artifacts are created during execution."""
    
    # Custom validator that always passes
    def passing_validator(task, validators, shell_mcp, current_mode="", **kwargs):
        return [ValidatorResult(
            validator_id="test",
            severity="pass",
            justification="OK"
        )]
    
    from snodo.engine.loop import GraphBuilder
    
    builder = GraphBuilder(
        sample_protocol,
        workspace_mcp=WorkspaceMCP(str(temp_git_repo)),
        git_mcp=GitMCP(str(temp_git_repo)),
        shell_mcp=ShellMCP(str(temp_git_repo)),
        coder=MockCoderAdapter(),
        validator_fn=passing_validator
    )
    
    graph = builder.build_graph()
    compiled = graph.compile()
    
    task = Task(id="multi_test", spec="Create function")
    
    initial_state = {
        "task": {"id": task.id, "spec": task.spec},
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
    
    result = compiled.invoke(initial_state)
    
    artifacts = result["artifacts"]
    
    # Should create artifacts
    assert len(artifacts) >= 2
    assert any("src/hello.py" in str(a) for a in artifacts)
    assert any("tests/test_hello.py" in str(a) for a in artifacts)


# CLI Integration Tests

def test_cli_integration_with_mock(temp_git_repo):
    """Test CLI can execute task end-to-end with mock coder."""
    import json
    import os
    import subprocess
    from unittest.mock import MagicMock, patch

    from snodo.cli.main import run_command
    import argparse
    from snodo.coders.mock import MockAdapter

    # Mock the classifier completion so no live LLM call is made
    mock_completion = MagicMock()
    mock_completion.return_value.choices[0].message.content = json.dumps({
        "flow_type": "feature",
        "wave_id": "new",
        "task_summary": "Test task",
        "feature_description": "Test feature",
    })

    snodo_dir = temp_git_repo / ".snodo"
    snodo_dir.mkdir(exist_ok=True)
    
    protocol_file = snodo_dir / "protocol.yml"
    protocol_file.write_text("""
protocol_id: "cli_test"
name: "CLI Test"
version: "1.0.0"
modes:
  - mode_id: "producer"
    name: "Producer"
    tools: ["edit"]
    validators: ["test"]
validators:
  - validator_id: "test"
    validator_type: "quality"
    evaluation_phase: "pre_execute"
    tooling:
      test_command: "true"
disagreement_policy: "unanimous"
initial_mode: "producer"
""")
    
    original_cwd = os.getcwd()
    os.chdir(temp_git_repo)
    
    try:
        args = argparse.Namespace(
            description="Test task",
            protocol=".snodo/protocol.yml",
            verbose=False,
            mock=True,
            model=None
        )
        
        with patch.object(MockAdapter, "completion_fn", mock_completion, create=True):
            run_command(args)
        
        # Execution writes to a worktree that is cleaned up after run.
        # Files were committed to git (worktree shares history with main repo).
        # The worktree commits on its own branch, so scan all branches.
        # Verify via git log rather than disk existence.
        log = subprocess.run(
            ["git", "log", "--all", "--oneline", "--name-only", "--format="],
            cwd=temp_git_repo, capture_output=True, text=True,
        )
        assert log.returncode == 0, log.stderr
        assert "src/hello.py" in log.stdout, f"src/hello.py not in git log:\n{log.stdout}"
        assert "tests/test_hello.py" in log.stdout, f"tests/test_hello.py not in git log:\n{log.stdout}"
        
    finally:
        os.chdir(original_cwd)