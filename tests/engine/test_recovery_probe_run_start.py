"""Inherited plan waves are not output from an empty coder dispatch."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from snodo.coders.base import CoderTimeoutError, TurnBudgetExhausted
from snodo.compiler.models import DisagreementPolicy, Mode, Protocol, Validator
from snodo.core.interfaces import CodeArtifact

from tests.engine.test_bounded_run_work_is_judged import (
    _builder,
    _git,
    _init_repo,
    _passing_validators,
    _state,
)


@pytest.fixture
def protocol():
    return Protocol(
        protocol_id="run_start", name="Run start", version="1.0.0",
        initial_mode="producer",
        modes=[Mode(mode_id="producer", name="Producer", tools=["edit"],
                    validators=["security", "acceptance"])],
        validators=[
            Validator(
                validator_id="security", validator_type="security",
                evaluation_phase="pre_execute", criteria=["Check the change"],
            ),
            Validator(
                validator_id="acceptance", validator_type="acceptance",
                evaluation_phase="post_execute", severity_cap="warn",
                criteria=["Judge the branch"],
            ),
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
    )


@pytest.fixture
def plan_repo(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    _init_repo(project)
    _git("git", "checkout", "-qb", "plan/example/integration", cwd=project)
    (project / "earlier.py").write_text("# Earlier wave\n")
    _git("git", "add", ".", cwd=project)
    _git("git", "commit", "-qm", "earlier wave", cwd=project)
    integration = _git("git", "rev-parse", "HEAD", cwd=project).stdout.strip()
    wt = tmp_path / ".snodo-worktrees" / "task_bounded"
    wt.parent.mkdir()
    _git("git", "worktree", "add", "-b", "task/example/task_bounded/feature",
         str(wt), "HEAD", cwd=project)
    monkeypatch.setenv("SNODO_PLAN_INTEGRATION_BRANCH", "plan/example/integration")
    return project, wt, integration


def _run(protocol, wt, *, action=None, snapshot=True):
    coder = SimpleNamespace(
        implement=MagicMock(side_effect=action or (lambda spec: CodeArtifact(files=[]))),
        skip_workspace_write=True, skip_engine_commit=True,
        last_read_paths=[], last_listed_dirs=[], last_findings=None,
        last_report=None,
    )
    audit = MagicMock()
    builder = _builder(protocol, str(wt), coder, _passing_validators, audit)
    probe = MagicMock(wraps=builder._existing_task_branch_work)
    builder._existing_task_branch_work = probe
    if not snapshot:
        real_snapshot = builder._repo_ref_snapshot

        def without_active_branch(git):
            refs = real_snapshot(git)
            refs.pop(git.repo.active_branch.name, None)
            return refs

        builder._repo_ref_snapshot = without_active_branch
    state = _state()
    state["task"].update(plan_name="example", base="main")
    result = builder.build_graph().compile().invoke(state)
    # No assertion below may pass merely because WF3 rejected the fixture.
    coder.implement.assert_called_once()
    probe.assert_called_once()
    assert probe.call_args.kwargs["task"].plan_name == "example"
    return result, audit


def test_empty_run_does_not_recover_inherited_commits(protocol, plan_repo):
    _, wt, _ = plan_repo
    result, _ = _run(protocol, wt)
    assert result["is_blocked"] is True
    assert result["halt_type"] == "no_file_operations"
    assert result["metadata"]["post_validation"]["outcome"] == "skipped"


def test_inherited_history_does_not_hide_workspace_escape(protocol, plan_repo):
    project, wt, _ = plan_repo
    external = project.parent / "external"
    _git("git", "worktree", "add", "-b", "outside", str(external), "HEAD", cwd=project)

    def escape(spec):
        (external / "escaped.py").write_text("# Outside the granted checkout\n")
        _git("git", "add", ".", cwd=external)
        _git("git", "commit", "-qm", "escaped", cwd=external)
        return CodeArtifact(files=[])

    result, _ = _run(protocol, wt, action=escape)
    assert result["is_blocked"] is True
    assert result["is_complete"] is False
    # The engine owns fault classification; this regression checks that the
    # escape is surfaced rather than hidden by recovery of inherited history.
    assert result["halt_type"] != "no_file_operations"
    violations = " ".join(result["constraint_violations"])
    assert "Workspace escape detected" in violations
    assert "ref 'outside'" in violations
    assert str(external.resolve()) in violations


def test_inherited_commits_make_no_recovered_work_claim(protocol, plan_repo, capsys):
    _, wt, _ = plan_repo
    result, audit = _run(protocol, wt)
    assert result["halt_type"] == "no_file_operations"
    assert "work_already_present" not in [c.args[0] for c in audit.append_event.call_args_list]
    output = capsys.readouterr().out
    assert "earlier attempt committed" not in output
    assert "Work already present on the task branch" not in output


def test_retry_recovers_its_own_previous_attempt(protocol, plan_repo):
    _, wt, integration = plan_repo
    (wt / "feature.py").write_text("answer = 42\n")
    _git("git", "add", ".", cwd=wt)
    _git("git", "commit", "-qm", "previous attempt", cwd=wt)
    result, audit = _run(protocol, wt)
    assert result["is_blocked"] is False
    assert result["metadata"]["post_validation"]["outcome"] == "passed"
    assert "feature.py" in result["artifacts"]
    assert "earlier.py" not in result["artifacts"]
    recovery = [c.args[1] for c in audit.append_event.call_args_list
                if c.args[0] == "work_already_present"]
    assert recovery[0]["base_ref"] == integration


def test_run_start_snapshot_recovers_only_dispatch_work(protocol, plan_repo, monkeypatch):
    _, wt, integration = plan_repo
    # The snapshot must suffice even when the integration ref is unavailable.
    monkeypatch.setenv("SNODO_PLAN_INTEGRATION_BRANCH", "missing-integration")

    def commit_work(spec):
        (wt / "feature.py").write_text("answer = 42\n")
        _git("git", "add", ".", cwd=wt)
        _git("git", "commit", "-qm", "this dispatch", cwd=wt)
        return CodeArtifact(files=[])

    result, audit = _run(protocol, wt, action=commit_work)
    assert result["is_blocked"] is False
    assert result["metadata"]["post_validation"]["outcome"] == "passed"
    assert "feature.py" in result["artifacts"]
    assert "earlier.py" not in result["artifacts"]
    recovery = [c.args[1] for c in audit.append_event.call_args_list
                if c.args[0] == "work_already_present"]
    assert recovery[0]["base_ref"] == integration


def test_missing_snapshot_uses_integration_merge_base(protocol, plan_repo):
    _, wt, _ = plan_repo
    result, _ = _run(protocol, wt, snapshot=False)
    assert result["halt_type"] == "no_file_operations"


def test_unresolved_integration_is_ambiguous(protocol, plan_repo, monkeypatch, capsys):
    _, wt, _ = plan_repo
    monkeypatch.setenv("SNODO_PLAN_INTEGRATION_BRANCH", "missing-integration")
    result, audit = _run(protocol, wt, snapshot=False)
    assert result["halt_type"] == "no_file_operations"
    records = [c.args[1] for c in audit.append_event.call_args_list
               if c.args[1].get("op") == "work_recovery_probe"]
    assert records[0]["verdict"] == "ambiguous"
    assert "ambiguous" in capsys.readouterr().out


@pytest.mark.parametrize("bound", [CoderTimeoutError, TurnBudgetExhausted])
def test_bounded_empty_run_does_not_recover_plan_history(protocol, plan_repo, bound):
    _, wt, _ = plan_repo

    def bounded(spec):
        raise bound("run bound reached")

    result, audit = _run(protocol, wt, action=bounded)
    assert result["is_blocked"] is True
    assert result["halt_type"] == (
        "environment_error" if bound is CoderTimeoutError else "turn_budget_exhausted"
    )
    assert result["metadata"]["post_validation"]["outcome"] == "skipped"
    assert "work_already_present" not in [c.args[0] for c in audit.append_event.call_args_list]
