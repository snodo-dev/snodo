"""A retried task whose work is already on its branch is not no_file_operations.

FILE: tests/engine/test_preexisting_task_work.py (Fixes #221)

PROVES:
- When a task commits work and then fails afterwards, the retry executes on a
  task branch that already carries that commit. A coder that finds the work
  already present correctly writes nothing. The engine must NOT report that as
  "Coder produced no file operations": no_file_operations means the work does
  not exist, not that this particular attempt did not create it.
- The retry instead reports the work as already present, carries the existing
  artifacts into post-execute validation exactly as freshly produced ones
  would be, and points the post-execute judges at the branch base so they
  diff base_ref..HEAD (the committed work).
- A coder that produced nothing on an unchanged branch (HEAD not ahead of the
  base) is still no_file_operations.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from snodo.compiler.models import DisagreementPolicy, ExecutionConfig, Mode, Protocol, Validator
from snodo.core.interfaces import CodeArtifact, ValidatorResult
from snodo.engine.loop import GraphBuilder
from snodo.infrastructure.tokens import TokenIssuer
from snodo.infrastructure.worktree import create_worktree
from snodo.tools.git import GitMCP
from snodo.tools.workspace import WorkspaceMCP

from tests.conftest import TEST_SECRET


@pytest.fixture
def protocol_with_post_execute():
    return Protocol(
        protocol_id="preexisting_work",
        name="Pre-existing Work",
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
                criteria=["Judge the produced artifacts"],
            ),
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer",
    )


def _git(*args, cwd):
    return subprocess.run(
        list(args), cwd=cwd, capture_output=True, text=True, check=True,
    )


def _init_repo(project: Path) -> None:
    _git("git", "init", "-q", "-b", "main", cwd=project)
    _git("git", "config", "user.email", "test@test.com", cwd=project)
    _git("git", "config", "user.name", "Test", cwd=project)
    (project / "README.md").write_text("# Fixture\n")
    _git("git", "add", ".", cwd=project)
    _git("git", "commit", "-qm", "initial", cwd=project)


class NoOpCoder:
    """A coder that finds the work already present and writes nothing.

    Mirrors the in-place adapters' empty-artifact behaviour (skip flags True:
    the adapter owns its own commit surface and never routes through
    WorkspaceMCP).
    """

    skip_workspace_write = True
    skip_engine_commit = True
    model = "mock-model"
    workspace_mcp = None
    progress_callback = None
    _job_id = ""
    _task_id = ""
    _depth = 0
    _attempt = 1

    def implement(self, spec):
        return CodeArtifact(files=[])


def _state(task_id="task_retry", spec="Implement feature X"):
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


def _task_project_with_commit(tmp_path, *, task_id="task_retry", branch=None, commit_work=True):
    """Create a project whose task worktree branch is ahead of ``main``.

    Returns (project_root, worktree_path, base_sha). When *commit_work* is
    True a commit sits on the task branch (an earlier attempt committed its
    work and then the run failed afterwards); when False the branch is fresh
    off the base and genuinely empty.
    """
    project = tmp_path / "project"
    project.mkdir()
    _init_repo(project)

    wt = Path(create_worktree(
        str(project), task_id, "Implement feature X", branch=branch,
    ))

    if commit_work:
        (wt / "feature.py").write_text("def feature():\n    return 42\n")
        (wt / "tests").mkdir()
        (wt / "tests" / "test_feature.py").write_text(
            "from feature import feature\n\n\ndef test_feature():\n    assert feature() == 42\n"
        )
        _git("git", "add", "-A", cwd=wt)
        _git("git", "commit", "-qm", "coder: apply changes", cwd=wt)

    base_sha = _git("git", "rev-parse", "main", cwd=project).stdout.strip()
    return str(project), str(wt), base_sha


def test_retry_reports_preexisting_work_and_post_validates_it(
    protocol_with_post_execute, tmp_path, capsys
):
    """A no-op retry on a task branch that already carries the work reports it
    as already present (not no_file_operations) and post-execute validates it."""
    _, wt, base_sha = _task_project_with_commit(tmp_path)
    head_sha = _git("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
    assert head_sha != base_sha  # the branch is ahead of the base

    workspace_mcp = WorkspaceMCP(wt)
    git_mcp = GitMCP(wt)

    post_calls = []

    def capturing_validator(task, validators, shell_mcp, current_mode="", **kwargs):
        if kwargs.get("phase") == "post_execute":
            post_calls.append(kwargs)
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
        coder=NoOpCoder(),
        worktree_path=wt,
        validator_fn=capturing_validator,
        token_issuer=TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600),
        audit_log=audit,
    )

    graph = builder.build_graph().compile()
    result = graph.invoke(_state())

    # Not a no_file_operations halt: the run proceeds and completes.
    assert result["is_blocked"] is False
    assert result["halt_type"] is None
    assert result["metadata"]["post_validation"]["outcome"] == "passed"

    # The pre-existing work was carried into the run as produced artifacts.
    assert "feature.py" in result["artifacts"]

    # Post-execute validation ran against the existing work: it received the
    # produced artifacts and a base_ref pointing at the branch base, so a
    # diffing judge reviews base_ref..HEAD = the committed work, not an empty
    # HEAD..HEAD from this no-op attempt.
    assert len(post_calls) == 1
    assert "feature.py" in post_calls[0]["artifacts"]
    assert post_calls[0]["base_ref"] == base_sha

    # The run says plainly that the work already existed, and audits it.
    captured = capsys.readouterr().out
    assert "Work already present on the task branch" in captured
    audit.append_event.assert_any_call("work_already_present", {
        "op": "work_already_present",
        "task_ref": "task_retry",
        "base_ref": base_sha,
        "artifacts_count": 2,
        "files": ["feature.py", "tests/test_feature.py"],
    })
    ops = [call.args[0] for call in audit.append_event.call_args_list]
    assert "no_file_operations" not in ops


def test_noop_on_unchanged_task_branch_stays_no_file_operations(
    protocol_with_post_execute, tmp_path
):
    """A coder that produced nothing on an unchanged branch is still
    no_file_operations — the fix must not change that classification."""
    project, wt, base_sha = _task_project_with_commit(tmp_path, commit_work=False)
    head_sha = _git("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
    assert head_sha == base_sha  # genuinely empty branch

    workspace_mcp = WorkspaceMCP(wt)
    git_mcp = GitMCP(wt)

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
        coder=NoOpCoder(),
        worktree_path=wt,
        validator_fn=_all_pass,
        token_issuer=TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600),
        audit_log=audit,
    )

    graph = builder.build_graph().compile()
    result = graph.invoke(_state())

    assert result["is_blocked"] is True
    assert result["halt_type"] == "no_file_operations"
    payload = result["metadata"]["halt_payload"]
    assert payload["reason"] is not None
    assert "Coder produced no file operations" in payload["reason"]
    ops = [call.args[0] for call in audit.append_event.call_args_list]
    assert "work_already_present" not in ops


def test_nondefault_branch_prefix_still_recovers_preexisting_work(
    protocol_with_post_execute, tmp_path, capsys
):
    """A project that configures execution.branch_prefix != "task" still gets the
    recovered-work guard: the prefix is read from the compiled protocol rather
    than assumed, so committed work under the configured prefix is not silently
    dropped on retry (Fixes #222)."""
    protocol = protocol_with_post_execute.model_copy(update={
        "execution": ExecutionConfig(branch_prefix="feature"),
    })

    _, wt, base_sha = _task_project_with_commit(
        tmp_path, branch="feature/task_retry/implement-feature-x",
    )
    head_sha = _git("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
    assert head_sha != base_sha  # the branch is ahead of the base
    branch = _git("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=wt).stdout.strip()
    assert branch.startswith("feature/")  # non-default prefix, not "task/"

    workspace_mcp = WorkspaceMCP(wt)
    git_mcp = GitMCP(wt)

    def _all_pass(task, validators, shell_mcp, current_mode="", **kwargs):
        return [
            ValidatorResult(validator_id=v.validator_id, severity="pass",
                            justification="ok")
            for v in validators
        ]

    audit = MagicMock()
    builder = GraphBuilder(
        protocol,
        workspace_mcp=workspace_mcp,
        git_mcp=git_mcp,
        shell_mcp=None,
        coder=NoOpCoder(),
        worktree_path=wt,
        validator_fn=_all_pass,
        token_issuer=TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600),
        audit_log=audit,
    )

    graph = builder.build_graph().compile()
    result = graph.invoke(_state())

    # Not a no_file_operations halt: the configured-prefix branch's committed
    # work was recognised and carried into the run.
    assert result["is_blocked"] is False
    assert result["halt_type"] is None
    assert result["metadata"]["post_validation"]["outcome"] == "passed"
    assert "feature.py" in result["artifacts"]

    captured = capsys.readouterr().out
    assert "Work already present on the task branch" in captured
    audit.append_event.assert_any_call("work_already_present", {
        "op": "work_already_present",
        "task_ref": "task_retry",
        "base_ref": base_sha,
        "artifacts_count": 2,
        "files": ["feature.py", "tests/test_feature.py"],
    })
    ops = [call.args[0] for call in audit.append_event.call_args_list]
    assert "no_file_operations" not in ops
