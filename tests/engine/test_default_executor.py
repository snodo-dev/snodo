"""Characterization tests for _default_executor.

FILE: tests/engine/test_default_executor.py
"""

import pytest
from unittest.mock import MagicMock, patch
from snodo.compiler.models import Protocol
from snodo.core.interfaces import Task, ExecutionError
from snodo.engine.loop import GraphBuilder
from snodo.infrastructure.tokens import ValidationToken


@pytest.fixture
def sample_protocol():
    from snodo.compiler.models import Mode, Validator
    return Protocol(
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
                criteria=["Check security"]
            )
        ],
        initial_mode="producer"
    )



@pytest.fixture
def sample_task():
    return Task(id="task_001", spec="Implement feature X")


@pytest.fixture
def mock_token():
    return MagicMock(spec=ValidationToken)


class DummyCoder:
    def __init__(self, files=None, workspace_mcp=None):
        self.workspace_mcp = workspace_mcp
        self.files = files or []
        self.skip_workspace_write = False
        self.skip_engine_commit = False
        self._job_id = None
        self._task_id = None

    def implement(self, spec):
        class CodeArtifact:
            def __init__(self, files):
                self.files = files
        return CodeArtifact(self.files)


class DummyFileOp:
    def __init__(self, action, path, content=""):
        self.action = action
        self.path = path
        self.content = content


def test_workspace_injection(sample_protocol, sample_task, mock_token):
    """workspace injection: coder.workspace_mcp is None -> gets set from workspace_mcp"""
    coder = DummyCoder(workspace_mcp=None)
    workspace_mcp = MagicMock()
    builder = GraphBuilder(sample_protocol)
    
    # Just run it (using mock implement return with empty files and skip_engine_commit = True to avoid raise)
    coder.skip_engine_commit = True
    builder._default_executor(sample_task, mock_token, coder, workspace_mcp, None)
    
    assert coder.workspace_mcp is workspace_mcp


def test_branch_isolation_branch_exists(sample_protocol, sample_task, mock_token):
    """branch isolation: branch exists -> checkout_branch"""
    coder = DummyCoder(workspace_mcp=None)
    coder.skip_engine_commit = True
    workspace_mcp = MagicMock()
    git_mcp = MagicMock()
    builder = GraphBuilder(sample_protocol)
    
    with patch("snodo.engine.nodes.executor._branch_exists", return_value=True):
        builder._default_executor(sample_task, mock_token, coder, workspace_mcp, git_mcp)
        
    git_mcp.checkout_branch.assert_called_once()
    git_mcp.create_branch.assert_not_called()


def test_branch_isolation_branch_absent(sample_protocol, sample_task, mock_token):
    """branch isolation: branch absent -> create_branch"""
    coder = DummyCoder(workspace_mcp=None)
    coder.skip_engine_commit = True
    workspace_mcp = MagicMock()
    git_mcp = MagicMock()
    builder = GraphBuilder(sample_protocol)
    
    with patch("snodo.engine.nodes.executor._branch_exists", return_value=False):
        builder._default_executor(sample_task, mock_token, coder, workspace_mcp, git_mcp)
        
    git_mcp.checkout_branch.assert_not_called()
    git_mcp.create_branch.assert_called_once()


def test_branch_isolation_worktree_set(sample_protocol, sample_task, mock_token):
    """branch isolation: self._worktree_path set -> branch block SKIPPED"""
    coder = DummyCoder(workspace_mcp=None)
    coder.skip_engine_commit = True
    workspace_mcp = MagicMock()
    git_mcp = MagicMock()
    builder = GraphBuilder(sample_protocol)
    builder._worktree_path = "/tmp/worktree"
    
    builder._default_executor(sample_task, mock_token, coder, workspace_mcp, git_mcp)
    
    git_mcp.checkout_branch.assert_not_called()
    git_mcp.create_branch.assert_not_called()


def test_branch_isolation_worktree_degraded(sample_protocol, sample_task, mock_token):
    """branch isolation: self._worktree_degraded True -> SKIPPED"""
    coder = DummyCoder(workspace_mcp=None)
    coder.skip_engine_commit = True
    workspace_mcp = MagicMock()
    git_mcp = MagicMock()
    builder = GraphBuilder(sample_protocol)
    builder._worktree_degraded = True
    
    builder._default_executor(sample_task, mock_token, coder, workspace_mcp, git_mcp)
    
    git_mcp.checkout_branch.assert_not_called()
    git_mcp.create_branch.assert_not_called()


def test_file_ops_write_and_delete(sample_protocol, sample_task, mock_token):
    """file ops: delete/write called appropriately according to skip flags, paths appended"""
    workspace_mcp = MagicMock()
    
    # 1. skip_workspace_write = False
    coder = DummyCoder([
        DummyFileOp("delete", "file1.txt"),
        DummyFileOp("write", "file2.txt", "content2")
    ])
    builder = GraphBuilder(sample_protocol)
    artifacts = builder._default_executor(sample_task, mock_token, coder, workspace_mcp, None)
    
    workspace_mcp.delete_file.assert_called_once_with("file1.txt")
    workspace_mcp.write_file.assert_called_once_with("file2.txt", "content2")
    assert "file1.txt" in artifacts
    assert "file2.txt" in artifacts

    # 2. skip_workspace_write = True
    workspace_mcp.reset_mock()
    coder_skip = DummyCoder([
        DummyFileOp("delete", "file1.txt"),
        DummyFileOp("write", "file2.txt", "content2")
    ])
    coder_skip.skip_workspace_write = True
    artifacts_skip = builder._default_executor(sample_task, mock_token, coder_skip, workspace_mcp, None)
    
    workspace_mcp.delete_file.assert_not_called()
    workspace_mcp.write_file.assert_not_called()
    assert "file1.txt" in artifacts_skip
    assert "file2.txt" in artifacts_skip


def test_empty_artifacts_error(sample_protocol, sample_task, mock_token):
    """empty artifacts + skip_engine_commit False -> raises ExecutionError"""
    coder = DummyCoder([])
    coder.skip_engine_commit = False
    workspace_mcp = MagicMock()
    builder = GraphBuilder(sample_protocol)
    
    with pytest.raises(ExecutionError, match="Coder produced no file operations"):
        builder._default_executor(sample_task, mock_token, coder, workspace_mcp, None)


def test_empty_artifacts_warning(sample_protocol, sample_task, mock_token):
    """empty artifacts + skip_engine_commit True -> "empty_artifact_warning" audit, no raise"""
    coder = DummyCoder([])
    coder.skip_engine_commit = True
    workspace_mcp = MagicMock()
    mock_audit = MagicMock()
    builder = GraphBuilder(sample_protocol, audit_log=mock_audit)
    
    builder._default_executor(sample_task, mock_token, coder, workspace_mcp, None)
    mock_audit.append_event.assert_any_call("empty_artifact_warning", {
        "op": "empty_artifact_warning",
        "task_ref": sample_task.id,
        "note": "OpenCode completed with no file changes — verify task was necessary",
    })


def test_git_path_success_and_error(sample_protocol, sample_task, mock_token):
    """git path: artifacts present + skip_engine_commit False -> stage + commit; git op raises -> error logged"""
    workspace_mcp = MagicMock()
    git_mcp = MagicMock()
    
    # 1. Success
    coder = DummyCoder([DummyFileOp("write", "file1.txt")])
    builder = GraphBuilder(sample_protocol)
    artifacts = builder._default_executor(sample_task, mock_token, coder, workspace_mcp, git_mcp)
    
    git_mcp.stage_files.assert_called_once_with(["file1.txt"])
    git_mcp.commit.assert_called_once_with(f"feat: {sample_task.spec}")
    assert "git_commit" in artifacts

    # 2. Git raises error
    git_mcp.reset_mock()
    git_mcp.stage_files.side_effect = Exception("Git went wrong")
    artifacts_err = builder._default_executor(sample_task, mock_token, coder, workspace_mcp, git_mcp)
    
    assert "git_error: Git went wrong" in artifacts_err


def test_no_workspace_mcp(sample_protocol, sample_task, mock_token):
    """no workspace_mcp -> "code_generated_for_<task.id>" stub appended"""
    coder = DummyCoder([DummyFileOp("write", "file1.txt")])
    builder = GraphBuilder(sample_protocol)
    artifacts = builder._default_executor(sample_task, mock_token, coder, None, None)
    
    assert f"code_generated_for_{sample_task.id}" in artifacts


def test_coder_implement_generic_exception(sample_protocol, sample_task, mock_token):
    """coder.implement raises generic Exception -> "error: ..." appended -> raises ExecutionError"""
    class BadCoder:
        def implement(self, spec):
            raise ValueError("Something went wrong in generation")
            
    builder = GraphBuilder(sample_protocol)
    with pytest.raises(ExecutionError, match="Coder execution failed:"):
        builder._default_executor(sample_task, mock_token, BadCoder(), MagicMock(), None)


def test_coder_implement_execution_error(sample_protocol, sample_task, mock_token):
    """coder.implement raises ExecutionError -> re-raised unchanged (pass-through)"""
    class BadCoder:
        def implement(self, spec):
            raise ExecutionError("Execution failed cleanly")
            
    builder = GraphBuilder(sample_protocol)
    with pytest.raises(ExecutionError, match="Execution failed cleanly"):
        builder._default_executor(sample_task, mock_token, BadCoder(), MagicMock(), None)
