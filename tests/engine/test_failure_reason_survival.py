"""Failure-reason survival tests for the executor node and readiness checker.

FILE: tests/engine/test_failure_reason_survival.py

The executor's work-recovery probe answers "no recoverable work" — a
verdict that misattributes a run as no_file_operations — when git merely
failed to answer. The readiness checker answers "not committed" — a
BLOCKER-grade finding about the project — when git failed to run at all.
These tests induce the tool failure and assert the reason survives to
the log, and (for readiness) to a surfaced finding: "could not ask git"
must be distinguishable from "git says no".
"""

import logging
import subprocess
from types import SimpleNamespace

from snodo.compiler.models import (
    DisagreementPolicy,
    Mode,
    Protocol,
    Validator,
)
from snodo.engine.nodes.executor import ExecutorMixin
from snodo.readiness import checker


def _joined(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


class _StubExecutor(ExecutorMixin):
    protocol = None


class _GitMCP:
    project_root = "/nonexistent-work-probe"

    def __init__(self, repo):
        self._repo = repo

    @property
    def repo(self):
        return self._repo


class TestExecutorWorkRecoveryProbe:
    def test_branch_name_failure_logs_reason(self, caplog):
        class _Repo:
            @property
            def active_branch(self):
                raise ValueError("reference is ambiguous: 'HEAD'")

        executor = _StubExecutor()
        with caplog.at_level(logging.WARNING, logger="snodo.engine.nodes.executor"):
            assert executor._existing_task_branch_work(_GitMCP(_Repo())) is None

        assert "active branch name unreadable" in _joined(caplog)
        assert "reference is ambiguous" in _joined(caplog)
        assert "ValueError" in _joined(caplog)

    def test_base_resolution_failure_logs_reason(self, caplog):
        class _Repo:
            active_branch = SimpleNamespace(name="task/t1-attempt-2")
            head = property(lambda self: SimpleNamespace(commit=SimpleNamespace(hexsha="a" * 40)))

            def commit(self, ref):
                return SimpleNamespace(hexsha="b" * 40)

        executor = _StubExecutor()
        # resolve_base_branch fails (broken repo config, missing remote HEAD)
        with caplog.at_level(logging.WARNING, logger="snodo.engine.nodes.executor"):
            import snodo.tools.git as git_tool
            original = git_tool.resolve_base_branch
            git_tool.resolve_base_branch = lambda root: (_ for _ in ()).throw(
                RuntimeError("no default branch: cannot resolve origin/HEAD")
            )
            try:
                assert executor._existing_task_branch_work(_GitMCP(_Repo())) is None
            finally:
                git_tool.resolve_base_branch = original

        assert "branch/base inspection failed" in _joined(caplog)
        assert "cannot resolve origin/HEAD" in _joined(caplog)

    def test_diff_inspection_failure_logs_reason(self, caplog):
        class _Repo:
            active_branch = SimpleNamespace(name="task/t1-attempt-2")
            head = property(lambda self: SimpleNamespace(commit=SimpleNamespace(hexsha="a" * 40)))

            def commit(self, ref):
                return SimpleNamespace(hexsha="b" * 40)

            def is_ancestor(self, a, b):
                raise RuntimeError("ls-tree failed: exit 128")

        executor = _StubExecutor()
        import snodo.tools.git as git_tool
        original = git_tool.resolve_base_branch
        git_tool.resolve_base_branch = lambda root: "main"
        try:
            with caplog.at_level(logging.WARNING, logger="snodo.engine.nodes.executor"):
                assert executor._existing_task_branch_work(_GitMCP(_Repo())) is None
        finally:
            git_tool.resolve_base_branch = original

        assert "ancestry/diff inspection failed" in _joined(caplog)
        assert "ls-tree failed: exit 128" in _joined(caplog)

    def test_prefix_config_read_failure_logs_reason(self, caplog):
        class _BadProtocol:
            @property
            def execution(self):
                raise RuntimeError("protocol field unreadable")

        executor = _StubExecutor()
        executor.protocol = _BadProtocol()  # type: ignore[assignment]
        with caplog.at_level(logging.WARNING, logger="snodo.engine.nodes.executor"):
            assert executor._task_branch_prefix() == "task"

        assert "Could not read execution.branch_prefix" in _joined(caplog)
        assert "protocol field unreadable" in _joined(caplog)


# ---------------------------------------------------------------------------
# Readiness: a git tool failure must not read as a verdict about the project
# ---------------------------------------------------------------------------

_SPAWN_MSG = "Failed to spawn: 'git': No such file or directory"


class _BrokenGitRepo:
    """A Repo object whose git subprocess invocations all fail to spawn."""

    def __init__(self):
        self.head = SimpleNamespace(is_valid=lambda: True)
        self.git = SimpleNamespace(
            ls_tree=lambda *a: (_ for _ in ()).throw(RuntimeError(_SPAWN_MSG))
        )


def _minimal_protocol() -> Protocol:
    validator = Validator(
        validator_id="val_quality",
        validator_type="quality",
        tooling={"test_command": "pytest"},
    )
    mode = Mode(mode_id="build", name="Build", validators=["val_quality"])
    return Protocol(
        protocol_id="reason-proto",
        name="Reason Proto",
        version="1.0.0",
        modes=[mode],
        validators=[validator],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="build",
        roles=[],
    )


class TestReadinessGitProbes:
    def test_ls_tree_failure_is_unknown_not_no(self, caplog):
        problems: list[str] = []
        with caplog.at_level(logging.DEBUG, logger="snodo.readiness.checker"):
            state = checker._is_path_committed(_BrokenGitRepo(), "docs/decisions", problems)

        # Distinguishable from the ordinary negative answer...
        assert state is None
        assert state is not False
        # ... the reason travels with the caller...
        assert any(_SPAWN_MSG in p for p in problems)
        # ... and reaches the log.
        assert _SPAWN_MSG in _joined(caplog)
        assert "RuntimeError" in _joined(caplog)

    def test_genuine_negative_stays_a_plain_false(self, tmp_path):
        """An ordinary 'not committed' answer is False with no recorded problem."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo_dir, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo_dir, check=True)
        (repo_dir / "f.txt").write_text("x")
        subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=repo_dir, check=True)

        from git import Repo

        problems: list[str] = []
        assert checker._is_path_committed(Repo(str(repo_dir)), "never/committed.txt", problems) is False
        assert problems == []

    def test_repo_open_failure_reason_recorded_not_confused_with_absent(self, monkeypatch, tmp_path, caplog):
        def boom(*args, **kwargs):
            raise OSError(_SPAWN_MSG)

        monkeypatch.setattr(checker, "Repo", boom)
        problems: list[str] = []
        with caplog.at_level(logging.DEBUG, logger="snodo.readiness.checker"):
            assert checker._get_git_repo(tmp_path, problems) is None

        assert any(_SPAWN_MSG in p for p in problems)
        assert _SPAWN_MSG in _joined(caplog)

    def test_not_a_repository_is_a_plain_negative_without_problem(self, tmp_path):
        problems: list[str] = []
        assert checker._get_git_repo(tmp_path, problems) is None
        assert problems == []  # "there is no repo" is an answer, not a failure


class TestReadinessSurfacesToolFailure:
    def test_assessment_names_git_tool_failure_instead_of_blaming_the_project(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            checker, "Repo", lambda *a, **k: _BrokenGitRepo()
        )
        assessment = checker.assess_readiness(tmp_path, _minimal_protocol())

        tool_failure = [
            f for f in assessment.repository_findings
            if f.id == "git_check_unavailable"
        ]
        assert tool_failure, (
            "a broken git must be surfaced as a tool failure finding"
        )
        # The reason — not just "git failed" — travels into the surfaced
        # message an operator reads in `snodo ready`.
        assert _SPAWN_MSG in tool_failure[0].description
        assert "may be wrong" in tool_failure[0].description


class TestDispatchFlatteningOutsideBoundary:
    """run_validators' future.result() catch keeps the reason too."""

    def test_dispatch_mechanism_failure_logs_type_and_message(self, caplog):
        from snodo.compiler.models import Mode as _Mode
        from snodo.compiler.models import Protocol as _Protocol
        from snodo.compiler.models import Validator as _Validator
        from snodo.core.interfaces import Task
        from snodo.validators.runner import run_validators

        validator = _Validator(
            validator_id="v1", validator_type="security", criteria=["c"]
        )
        mode = _Mode(mode_id="build", name="Build", validators=["v1"])
        protocol = _Protocol(
            protocol_id="p", name="p", version="1.0.0",
            modes=[mode], validators=[validator],
            disagreement_policy=DisagreementPolicy.UNANIMOUS,
            initial_mode="build", roles=[],
        )
        task = Task(id="t1", spec="s")

        def exploding_dispatch(v, ctx, reg):
            raise RuntimeError("dispatch machinery caught fire")

        with caplog.at_level(logging.WARNING, logger="snodo.validators.runner"):
            results, _ = run_validators(
                protocol, [validator], task, phase="pre_execute",
                dispatch_fn=exploding_dispatch,
            )

        assert results[0].error is True
        assert "dispatch machinery caught fire" in results[0].justification
        assert "RuntimeError" in results[0].justification
        assert "dispatch machinery caught fire" in _joined(caplog)
        assert "RuntimeError" in _joined(caplog)
