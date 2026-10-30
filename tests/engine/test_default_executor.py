"""Characterization tests for _default_executor.

FILE: tests/engine/test_default_executor.py
"""

from unittest.mock import MagicMock, patch

import pytest
from snodo.compiler.models import Protocol
from snodo.core.interfaces import ExecutionError, Task
from snodo.coders.base import (
    AdapterError, CoderTimeoutError, SnodoMutationError, TurnBudgetExhausted,
)
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
        if files is None:
            files = [DummyFileOp("write", "src/generated.py", "content")]
        self.files = files
        self.skip_workspace_write = False
        self.skip_engine_commit = False
        self._job_id = None
        self._task_id = None
        self.progress_callback = None

    def implement(self, spec):
        class CodeArtifact:
            def __init__(self, files):
                self.files = files
                self.findings = None

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


def test_empty_artifacts_always_raise(sample_protocol, sample_task, mock_token):
    """Empty artifacts raise ExecutionError regardless of skip_engine_commit.

    "Coder produced nothing" is the same fault whether the engine commits the
    artifacts (skip_engine_commit False) or the adapter commits them itself
    (skip_engine_commit True): opting out of the commit mechanism does not
    waive the obligation to produce observable work (#68).
    """
    workspace_mcp = MagicMock()
    builder = GraphBuilder(sample_protocol)

    for skip in (False, True):
        coder = DummyCoder([])
        coder.skip_engine_commit = skip
        with pytest.raises(ExecutionError, match="Coder produced no file operations"):
            builder._default_executor(sample_task, mock_token, coder, workspace_mcp, None)


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
    """coder.implement raises generic Exception -> raises ExecutionError with exact message"""
    class BadCoder:
        def implement(self, spec):
            raise ValueError("Something went wrong in generation")

    builder = GraphBuilder(sample_protocol)
    with pytest.raises(ExecutionError, match="Something went wrong in generation"):
        builder._default_executor(sample_task, mock_token, BadCoder(), MagicMock(), None)


def test_coder_implement_execution_error(sample_protocol, sample_task, mock_token):
    """coder.implement raises ExecutionError -> re-raised unchanged (pass-through)"""
    class BadCoder:
        def implement(self, spec):
            raise ExecutionError("Execution failed cleanly")

    builder = GraphBuilder(sample_protocol)
    with pytest.raises(ExecutionError, match="Execution failed cleanly"):
        builder._default_executor(sample_task, mock_token, BadCoder(), MagicMock(), None)


def test_delete_without_content_removes_file(sample_protocol, sample_task, mock_token):
    """A delete operation with no content removes the target file."""
    workspace_mcp = MagicMock()
    coder = DummyCoder([
        DummyFileOp("delete", "orphan_module.py")
    ])
    builder = GraphBuilder(sample_protocol)
    artifacts = builder._default_executor(sample_task, mock_token, coder, workspace_mcp, None)

    workspace_mcp.delete_file.assert_called_once_with("orphan_module.py")
    assert "orphan_module.py" in artifacts


def test_delete_nonexistent_path_not_a_crash(sample_protocol, sample_task, mock_token):
    """Deleting a path that does not exist on disk is not a crash."""
    workspace_mcp = MagicMock()
    workspace_mcp.delete_file.side_effect = FileNotFoundError("File not found: nonexistent.py")

    coder = DummyCoder([
        DummyFileOp("delete", "nonexistent.py")
    ])
    builder = GraphBuilder(sample_protocol)
    artifacts = builder._default_executor(sample_task, mock_token, coder, workspace_mcp, None)

    workspace_mcp.delete_file.assert_called_once_with("nonexistent.py")
    assert "nonexistent.py" in artifacts


def _git(cwd, *args):
    import subprocess

    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_escape_detector_ignores_engine_managed_worktrees(tmp_path, sample_protocol):
    from snodo.engine.loop import GraphBuilder
    from snodo.infrastructure.worktree import worktree_dir
    from snodo.tools.git import GitMCP

    root = (tmp_path / "project").resolve()
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "base.txt").write_text("base")
    _git(root, "add", "base.txt")
    _git(root, "commit", "-m", "initial")
    managed = worktree_dir(str(root)) / "plan" / "task"
    managed.parent.mkdir(parents=True)
    _git(root, "worktree", "add", "-b", "task/plan/task/feature", str(managed))
    builder = GraphBuilder(sample_protocol)
    mcp = GitMCP(str(root))
    try:
        before = builder._repo_ref_snapshot(mcp)
        (managed / "change.txt").write_text("managed")
        _git(managed, "add", "change.txt")
        _git(managed, "commit", "-m", "managed change")
        assert builder._escaped_repo_commit(mcp, before, Task(id="task", spec="")) is None
    finally:
        mcp.repo.close()


def test_escape_detector_names_external_checkout_and_changed_ref(tmp_path, sample_protocol):
    from snodo.engine.loop import GraphBuilder
    from snodo.tools.git import GitMCP

    root = (tmp_path / "project").resolve()
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "base.txt").write_text("base")
    _git(root, "add", "base.txt")
    _git(root, "commit", "-m", "initial")
    external = (tmp_path / "external-checkout").resolve()
    _git(root, "worktree", "add", "-b", "operator-branch", str(external))
    builder = GraphBuilder(sample_protocol)
    mcp = GitMCP(str(root))
    try:
        before = builder._repo_ref_snapshot(mcp)
        (external / "escape.txt").write_text("escaped")
        _git(external, "add", "escape.txt")
        _git(external, "commit", "-m", "external change")
        result = builder._escaped_repo_commit(mcp, before, Task(id="task", spec=""))
        assert result is not None
        assert str(external) in result
        assert "operator-branch" in result
    finally:
        mcp.repo.close()


# New tests for detector-level defensive paths


def test_repo_ref_snapshot_best_effort_on_unreadable_heads(sample_protocol):
    """_repo_ref_snapshot returns empty dict when heads enumeration fails.

    The detector interacts only with the handle it is given; a stand-in with
    a ``repo`` whose ``heads`` attribute raises exercises the best-effort
    defensive path without mutating a real GitPython Repo.
    """
    builder = GraphBuilder(sample_protocol)

    class BrokenRepo:
        @property
        def heads(self):  # pragma: no cover - trivial property
            raise RuntimeError("cannot enumerate heads")

    class Handle:
        def __init__(self):
            self.repo = BrokenRepo()

    snapshot = builder._repo_ref_snapshot(Handle())
    assert snapshot == {}


def test_escaped_repo_commit_changed_non_task_ref_inside_managed_container_not_escape(tmp_path, sample_protocol):
    """A changed non-task ref whose worktree is inside the managed container is not an escape.

    The detector must reach its "no external checkouts" conclusion: the only
    changed ref is a non-task branch whose worktree path resolves under the
    engine-managed container directory, so the guard must classify it as
    internal and report no escape.
    """
    from snodo.engine.loop import GraphBuilder
    from snodo.infrastructure.worktree import worktree_dir
    from snodo.tools.git import GitMCP

    root = (tmp_path / "project").resolve()
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "base.txt").write_text("base")
    _git(root, "add", "base.txt")
    _git(root, "commit", "-m", "initial")

    # Create a non-task branch whose worktree lives under the managed
    # container; this ensures the ref passes the "non-task" filter while the
    # checkout path is classified as internal.
    managed_container = worktree_dir(str(root)) / "plan" / "task"
    managed_container.mkdir(parents=True, exist_ok=True)
    feature_managed = managed_container / "feature-managed"
    feature_managed.mkdir(parents=True, exist_ok=True)

    _git(root, "worktree", "add", "-b", "feature-managed", str(feature_managed))

    builder = GraphBuilder(sample_protocol)
    mcp = GitMCP(str(root))
    try:
        before = builder._repo_ref_snapshot(mcp)
        (feature_managed / "change.txt").write_text("managed change")
        _git(feature_managed, "add", "change.txt")
        _git(feature_managed, "commit", "-m", "managed change")

        escaped = builder._escaped_repo_commit(mcp, before, Task(id="task", spec=""))
        assert escaped is None
    finally:
        mcp.repo.close()


def test_escaped_repo_commit_swallows_worktree_inspection_failure(tmp_path, sample_protocol):
    """A failure while enumerating worktrees is swallowed, not surfaced as an escape.

    The detector injects the failure at the collaborator it actually calls:
    the ``repo.git.worktree`` method on the handle it is given. The rest of
    the snapshotting and ref comparison run against a real temporary
    repository so porcelain parsing stays exercised.
    """
    from snodo.engine.loop import GraphBuilder
    from snodo.tools.git import GitMCP

    root = (tmp_path / "project").resolve()
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "base.txt").write_text("base")
    _git(root, "add", "base.txt")
    _git(root, "commit", "-m", "initial")

    # Create an external worktree so that, absent the injected failure, the
    # detector would classify the change as an escape.
    external = (tmp_path / "external-checkout").resolve()
    _git(root, "worktree", "add", "-b", "feature-external", str(external))

    builder = GraphBuilder(sample_protocol)
    base_mcp = GitMCP(str(root))

    class FailingGit:
        def __init__(self, real_git):
            self._real_git = real_git

        def __getattr__(self, name):  # pragma: no cover - delegation
            return getattr(self._real_git, name)

        def worktree(self, *args, **kwargs):  # pragma: no cover - trivial raise
            raise RuntimeError("simulated worktree inspection failure")

    class FailingHandle:
        def __init__(self, real_mcp):
            self.project_root = real_mcp.project_root
            self.repo = real_mcp.repo
            # Replace only the collaborator the detector calls.
            self.repo.git = FailingGit(real_mcp.repo.git)

    failing_mcp = FailingHandle(base_mcp)

    try:
        before = builder._repo_ref_snapshot(failing_mcp)
        (external / "escape.txt").write_text("escaped")
        _git(external, "add", "escape.txt")
        _git(external, "commit", "-m", "external change")

        escaped = builder._escaped_repo_commit(failing_mcp, before, Task(id="task", spec=""))
        assert escaped is None
    finally:
        base_mcp.repo.close()


def test_default_executor_halts_on_workspace_escape(tmp_path, sample_protocol):
    """Executor raises when the escape detector reports an external checkout change (#941).

    The coder produces no file operations and no findings, the task branch
    remains untouched, a ref snapshot is captured before dispatch, and a
    non-task ref whose checkout lives outside the managed container moves in
    between. Under these preconditions the executor must surface the escape
    as an ExecutionError whose message begins with "Workspace escape
    detected".
    """
    from snodo.engine.loop import GraphBuilder
    from snodo.tools.git import GitMCP

    # Set up a real repository under a temporary project root.
    root = (tmp_path / "project").resolve()
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "base.txt").write_text("base")
    _git(root, "add", "base.txt")
    _git(root, "commit", "-m", "initial")

    # Create an external (non-task) worktree as a sibling of the project root.
    external = (tmp_path / "external-checkout").resolve()
    _git(root, "worktree", "add", "-b", "operator-branch", str(external))

    builder = GraphBuilder(sample_protocol)
    git_mcp = GitMCP(str(root))

    class NoOpCoder(DummyCoder):
        def __init__(self):
            super().__init__(files=[])

        def implement(self, spec):
            class CodeArtifact:
                def __init__(self):
                    self.files = []
                    self.findings = None

            return CodeArtifact()

    coder = NoOpCoder()
    coder.skip_engine_commit = False

    # Use a real workspace MCP rooted at the project so that the executor can
    # apply file operations if it ever had any (it does not in this scenario).
    from snodo.tools.workspace import WorkspaceMCP

    workspace_mcp = WorkspaceMCP(str(root))

    # Capture the reference snapshot before the coder runs by calling the
    # private helper the executor itself uses. This ensures the base snapshot
    # exists even though the coder will not move any refs.
    before = builder._repo_ref_snapshot(git_mcp)
    assert before  # defensive: we expect at least "main" to be present

    # Now move the external non-task ref so the detector can report an escape.
    (external / "escape.txt").write_text("escaped")
    _git(external, "add", "escape.txt")
    _git(external, "commit", "-m", "external change")

    # Patch the snapshot helper on this builder instance so that the executor
    # reuses the "before" snapshot captured above instead of taking a fresh
    # one that would see the updated ref as its baseline.
    builder._repo_ref_snapshot = lambda mcp: before  # type: ignore[assignment]

    with pytest.raises(ExecutionError) as excinfo:
        builder._default_executor(Task(id="task_001", spec="Implement feature X"), MagicMock(), coder, workspace_mcp, git_mcp)

    assert str(excinfo.value).startswith("Workspace escape detected")

    git_mcp.repo.close()



@pytest.mark.parametrize("escaped", [True, False], ids=["escape", "clean"])
def test_escape_checked_before_nonempty_artifact_commit(sample_protocol, sample_task, mock_token, escaped):
    builder = GraphBuilder(sample_protocol)
    coder = DummyCoder([DummyFileOp("write", "result.py")])
    workspace = MagicMock()
    git = MagicMock()
    builder._escaped_repo_commit = lambda *args: "Workspace escape detected" if escaped else None
    if escaped:
        with pytest.raises(ExecutionError, match="Workspace escape"):
            builder._default_executor(sample_task, mock_token, coder, workspace, git)
        git.stage_files.assert_not_called()
        git.commit.assert_not_called()
    else:
        assert "result.py" in builder._default_executor(sample_task, mock_token, coder, workspace, git)
        git.commit.assert_called_once()


@pytest.mark.parametrize("escaped", [True, False], ids=["escape", "clean"])
def test_findings_only_escape_check(sample_protocol, sample_task, mock_token, escaped):
    builder = GraphBuilder(sample_protocol)
    coder = DummyCoder([])
    def implement(spec):
        class Artifact:
            files = []
            findings = ["finding"]
        return Artifact()
    coder.implement = implement
    builder._escaped_repo_commit = lambda *args: "Workspace escape detected" if escaped else None
    if escaped:
        with pytest.raises(ExecutionError, match="Workspace escape"):
            builder._default_executor(sample_task, mock_token, coder, MagicMock(), None)
    else:
        assert builder._default_executor(sample_task, mock_token, coder, MagicMock(), None) == []


@pytest.mark.parametrize("error", [
    pytest.param(CoderTimeoutError("timeout"), id="timeout"),
    pytest.param(TurnBudgetExhausted("budget"), id="turn-budget"),
])
@pytest.mark.parametrize("escaped", [True, False], ids=["escape", "clean"])
def test_bounded_failure_escape_preserves_or_reports_failure(sample_protocol, sample_task, mock_token, error, escaped):
    builder = GraphBuilder(sample_protocol)
    class BadCoder(DummyCoder):
        def implement(self, spec):
            raise error
    builder._escaped_repo_commit = lambda *args: "Workspace escape detected" if escaped else None
    if escaped:
        with pytest.raises(ExecutionError, match="Workspace escape"):
            builder._default_executor(sample_task, mock_token, BadCoder(), MagicMock(), None)
    else:
        with pytest.raises(type(error)):
            builder._default_executor(sample_task, mock_token, BadCoder(), MagicMock(), None)


@pytest.mark.parametrize("kind", ["mutation", "adapter"])
@pytest.mark.parametrize("escaped", [True, False], ids=["escape", "clean"])
def test_classified_failures_keep_exception_and_log_escape(sample_protocol, sample_task, mock_token, kind, escaped, caplog):
    builder = GraphBuilder(sample_protocol)
    failure = SnodoMutationError([".snodo/protocol.yml"]) if kind == "mutation" else AdapterError("adapter unavailable")
    class BadCoder(DummyCoder):
        def implement(self, spec):
            raise failure
    builder._escaped_repo_commit = lambda *args: "Workspace escape detected" if escaped else None
    with pytest.raises(type(failure)) as caught:
        builder._default_executor(sample_task, mock_token, BadCoder(), MagicMock(), None)
    assert caught.value is failure
    assert ("Workspace escape detected" in caplog.text) is escaped
