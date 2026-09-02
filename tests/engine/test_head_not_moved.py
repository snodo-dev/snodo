"""Canary: an adapter that returns file operations while its commit does not
happen halts with ``head_not_moved`` instead of letting the post-execute judges
review the previous commit and pass.

FILE: tests/engine/test_head_not_moved.py (Fixes #103)

Against current main this must show the run PASSING on the previous commit's
diff (HEAD~1..HEAD resolves to the previous unrelated commit when HEAD did not
move). After the fix the run halts with halt_type ``head_not_moved`` and a
distinct ``head_not_moved`` audit op.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from snodo.compiler.models import DisagreementPolicy, Mode, Protocol, Validator
from snodo.core.interfaces import ValidatorResult
from snodo.engine.loop import GraphBuilder
from snodo.tools.git import GitMCP
from snodo.tools.workspace import WorkspaceMCP

from tests.conftest import TEST_SECRET


@pytest.fixture
def temp_workspace():
    """Create a temporary git repository with an initial commit."""
    temp_dir = tempfile.mkdtemp()

    subprocess.run(["git", "init"], cwd=temp_dir, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=temp_dir, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=temp_dir, capture_output=True, check=True)

    readme = Path(temp_dir) / "README.md"
    readme.write_text("test")
    subprocess.run(["git", "add", "README.md"], cwd=temp_dir, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=temp_dir, capture_output=True, check=True)

    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def protocol_with_post_execute():
    return Protocol(
        protocol_id="head_not_moved",
        name="Head Not Moved",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit"],
                validators=["security", "acceptance"],
            )
        ],
        validators=[
            Validator(
                validator_id="security",
                validator_type="security",
                criteria=["Check the change"],
                evaluation_phase="pre_execute",
            ),
            Validator(
                validator_id="acceptance",
                validator_type="acceptance",
                evaluation_phase="post_execute",
                severity_cap="warn",
                tools=["read_file", "list_files", "read_diff_between_refs"],
                criteria=["Judge the produced artifacts"],
            ),
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer",
    )


def _state(task_id="task_head", spec="Implement feature X"):
    return {
        "task": {"id": task_id, "spec": spec},
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
        "metadata": {},
        "messages": [],
        "summary": "",
    }


def test_artifact_returned_without_commit_halts_head_not_moved(
    protocol_with_post_execute, temp_workspace, capsys
):
    """An executor that returns file operations but does not commit halts with
    head_not_moved and audits it under a distinct op."""
    from snodo.infrastructure.tokens import TokenIssuer

    workspace_mcp = WorkspaceMCP(temp_workspace)
    git_mcp = GitMCP(temp_workspace)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=temp_workspace,
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    # The coder claims a commit it never makes: returns file operations but
    # HEAD is unchanged (the repo had one commit before, and none is added).
    def executor_without_commit(task, token, coder, workspace_mcp, git_mcp, **kwargs):
        workspace_mcp.write_file("src/feature.py", "def feature(): pass")
        return ["src/feature.py"]

    def _all_pass(task, validators, shell_mcp, current_mode="", **kwargs):
        return [
            ValidatorResult(validator_id=v.validator_id, severity="pass",
                            justification="ok")
            for v in validators
        ]

    audit = MagicMock()
    builder = GraphBuilder(
        protocol_with_post_execute,
        workspace_mcp=workspace_mcp,
        git_mcp=git_mcp,
        shell_mcp=None,
        executor_fn=executor_without_commit,
        validator_fn=_all_pass,
        token_issuer=TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600),
        audit_log=audit,
    )

    # Full graph invoke: governance -> validate(pre) -> execute -> (halt).
    graph = builder.build_graph().compile()
    result = graph.invoke(_state())

    assert result["is_blocked"] is True
    assert result["halt_type"] == "head_not_moved"
    assert "HEAD did not move" in result["constraint_violations"][0]

    # The distinct audit op is recorded, not the generic blocked path.
    audit.append_event.assert_any_call("head_not_moved", {
        "op": "head_not_moved",
        "task_ref": "task_head",
        "base_ref": base_sha,
        "artifacts_count": 1,
        "commit_reason": "unknown",
    })
    # No generic validate/post_validate pass is emitted for a blocked head.
    ops = [call.args[0] for call in audit.append_event.call_args_list]
    assert "task_complete" not in ops


def test_executor_that_commits_moves_head_and_passes(
    protocol_with_post_execute, temp_workspace
):
    """A real executor commits; HEAD moves; no head_not_moved halt."""
    from snodo.infrastructure.tokens import TokenIssuer

    workspace_mcp = WorkspaceMCP(temp_workspace)
    git_mcp = GitMCP(temp_workspace)

    def executor_with_commit(task, token, coder, workspace_mcp, git_mcp, **kwargs):
        workspace_mcp.write_file("src/feature.py", "def feature(): pass")
        git_mcp.stage_files(["src/feature.py"])
        git_mcp.commit("feat: Implement feature X")
        return ["src/feature.py"]

    def _all_pass(task, validators, shell_mcp, current_mode="", **kwargs):
        return [
            ValidatorResult(validator_id=v.validator_id, severity="pass",
                            justification="ok")
            for v in validators
        ]

    builder = GraphBuilder(
        protocol_with_post_execute,
        workspace_mcp=workspace_mcp,
        git_mcp=git_mcp,
        shell_mcp=None,
        executor_fn=executor_with_commit,
        validator_fn=_all_pass,
        token_issuer=TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600),
    )

    graph = builder.build_graph().compile()
    result = graph.invoke(_state())

    assert result["is_blocked"] is False
    assert result["halt_type"] is None
    assert "src/feature.py" in result["artifacts"]


def test_inplace_adapter_git_add_failure_names_cause_in_halt_payload(
    protocol_with_post_execute, temp_workspace
):
    """Canary: driving an in-place adapter where git add fails halts with
    head_not_moved AND names git_add_failed in the halt payload."""
    from unittest.mock import patch

    from git import GitCommandError
    from snodo.coders.base import InPlaceCoderAdapter
    from snodo.core.interfaces import CodeArtifact, FileArtifact
    from snodo.infrastructure.tokens import TokenIssuer

    workspace_mcp = WorkspaceMCP(temp_workspace)
    git_mcp = GitMCP(temp_workspace)

    class DummyInPlaceCoder(InPlaceCoderAdapter):
        def __init__(self, workspace):
            self._workspace = Path(workspace)

        def _implement_in_place(self, spec):
            (self._workspace / "foo.py").write_text("print(1)")
            return CodeArtifact(files=[FileArtifact(path="foo.py", content="print(1)")])

    coder = DummyInPlaceCoder(temp_workspace)

    def _all_pass(task, validators, shell_mcp, current_mode="", **kwargs):
        return [
            ValidatorResult(validator_id=v.validator_id, severity="pass", justification="ok")
            for v in validators
        ]

    builder = GraphBuilder(
        protocol_with_post_execute,
        workspace_mcp=workspace_mcp,
        git_mcp=git_mcp,
        shell_mcp=None,
        coder=coder,
        validator_fn=_all_pass,
        token_issuer=TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600),
    )

    with patch("git.Repo") as mock_repo_cls:
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        mock_repo.git.add.side_effect = GitCommandError("git add", "fatal: pathspec failed")
        graph = builder.build_graph().compile()
        result = graph.invoke(_state())

    assert result["is_blocked"] is True
    assert result["halt_type"] == "head_not_moved"
    assert result["is_blocked"] is True
    assert result["halt_type"] == "head_not_moved"
    payload = result["metadata"]["halt_payload"]
    assert "commit_reason" in payload
    assert "git_add_failed" in payload["commit_reason"]


def test_inplace_adapter_cannot_open_repo_names_cause_in_halt_payload(
    protocol_with_post_execute, temp_workspace
):
    """Driving an in-place adapter where repo cannot be opened halts with
    head_not_moved AND names cannot_open_repo in the halt payload."""
    from unittest.mock import patch

    from snodo.coders.base import InPlaceCoderAdapter
    from snodo.core.interfaces import CodeArtifact, FileArtifact
    from snodo.infrastructure.tokens import TokenIssuer

    workspace_mcp = WorkspaceMCP(temp_workspace)
    git_mcp = GitMCP(temp_workspace)

    class DummyInPlaceCoder(InPlaceCoderAdapter):
        def __init__(self, workspace):
            self._workspace = Path(workspace)

        def _implement_in_place(self, spec):
            (self._workspace / "foo.py").write_text("print(1)")
            return CodeArtifact(files=[FileArtifact(path="foo.py", content="print(1)")])

    coder = DummyInPlaceCoder(temp_workspace)

    def _all_pass(task, validators, shell_mcp, current_mode="", **kwargs):
        return [
            ValidatorResult(validator_id=v.validator_id, severity="pass", justification="ok")
            for v in validators
        ]

    builder = GraphBuilder(
        protocol_with_post_execute,
        workspace_mcp=workspace_mcp,
        git_mcp=git_mcp,
        shell_mcp=None,
        coder=coder,
        validator_fn=_all_pass,
        token_issuer=TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600),
    )

    with patch("git.Repo", side_effect=Exception("Invalid repo")):
        graph = builder.build_graph().compile()
        result = graph.invoke(_state())

    assert result["is_blocked"] is True
    assert result["halt_type"] == "head_not_moved"
    payload = result["metadata"]["halt_payload"]
    assert "cannot_open_repo" in payload.get("commit_reason", "")


def test_inplace_adapter_nothing_staged_names_cause_in_halt_payload(
    protocol_with_post_execute, temp_workspace
):
    """Driving an in-place adapter where git add stages nothing halts with
    head_not_moved AND names nothing_staged in the halt payload."""
    from unittest.mock import MagicMock, patch

    from snodo.coders.base import InPlaceCoderAdapter
    from snodo.core.interfaces import CodeArtifact, FileArtifact
    from snodo.infrastructure.tokens import TokenIssuer

    workspace_mcp = WorkspaceMCP(temp_workspace)
    git_mcp = GitMCP(temp_workspace)

    class DummyInPlaceCoder(InPlaceCoderAdapter):
        def __init__(self, workspace):
            self._workspace = Path(workspace)

        def _implement_in_place(self, spec):
            return CodeArtifact(files=[FileArtifact(path="foo.py", content="print(1)")])

    coder = DummyInPlaceCoder(temp_workspace)

    def _all_pass(task, validators, shell_mcp, current_mode="", **kwargs):
        return [
            ValidatorResult(validator_id=v.validator_id, severity="pass", justification="ok")
            for v in validators
        ]

    builder = GraphBuilder(
        protocol_with_post_execute,
        workspace_mcp=workspace_mcp,
        git_mcp=git_mcp,
        shell_mcp=None,
        coder=coder,
        validator_fn=_all_pass,
        token_issuer=TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600),
    )

    with patch("git.Repo") as mock_repo_cls:
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        # diff --cached --quiet exits with 0 when nothing is staged
        mock_repo.git.diff.return_value = ""
        graph = builder.build_graph().compile()
        result = graph.invoke(_state())

    assert result["is_blocked"] is True
    assert result["halt_type"] == "head_not_moved"
    payload = result["metadata"]["halt_payload"]
    assert payload.get("commit_reason") == "nothing_staged"


def test_opencode_cli_diff_to_artifact_unreadable_file_distinguishable(temp_workspace):
    """Unreadable files in _diff_to_artifact produce <unreadable: ...> instead of empty string."""
    from snodo.coders.opencode_cli_adapter import OpenCodeCLIAdapter

    adapter = OpenCodeCLIAdapter(workspace=Path(temp_workspace))

    # Reference a non-existent file path
    artifact = adapter._diff_to_artifact([{"file": "non_existent_file.py", "status": "modified"}])
    assert len(artifact.files) == 1
    assert artifact.files[0].path == "non_existent_file.py"
    assert "<unreadable:" in artifact.files[0].content


