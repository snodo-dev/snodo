"""Both bounds a coder can hit have the produced work judged, not assumed absent.

FILE: tests/engine/test_bounded_run_work_is_judged.py (Fixes #282)

#281 fixed one bound: a run that ends on the clock carries recoverable work on
the task branch into post-execute validation instead of being reported as a
blocker. A run that exhausts its turn budget is the same kind of event — the
coder ran out of turns to *submit*, not to write — and must reach the same
probe. These tests exercise BOTH bounded outcomes together, so the two paths
cannot drift apart again:

- a turn-budget exhaustion whose work is already committed on the branch is
  post-validated, and its halt does not read as a verdict about the code;
- with nothing recoverable, both bounds halt under the operational
  ``environment_error`` (ADR 015), never a blocker about work no judge saw;
- the two exceptions resolve identically, which is the property that stops one
  from being "fixed" while the other is left behind.

PROVES:
- A turn-budget exhaustion with work on the branch reaches post-execute
  validation.
- The clock-bound and turn-bound paths classify and behave the same way.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from snodo.coders.agy_adapter import AGYAdapter
from snodo.coders.base import CoderTimeoutError, TurnBudgetExhausted
from snodo.compiler.models import DisagreementPolicy, Mode, Protocol, Validator
from snodo.core.interfaces import ValidatorResult
from snodo.engine.loop import GraphBuilder
from snodo.infrastructure.tokens import TokenIssuer
from snodo.infrastructure.worktree import create_worktree
from snodo.tools.git import GitMCP
from snodo.tools.workspace import WorkspaceMCP

from tests.conftest import TEST_SECRET

#: The two bounded outcomes a coder can raise when it hits a limit.
BOUNDED_OUTCOMES = [CoderTimeoutError, TurnBudgetExhausted]


@pytest.fixture
def protocol_with_post_execute():
    return Protocol(
        protocol_id="bounded_work",
        name="Bounded Work",
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


def _task_worktree(tmp_path, *, commit_work: bool):
    """A task worktree whose branch may already carry an earlier attempt's work."""
    project = tmp_path / "project"
    project.mkdir(parents=True)
    _init_repo(project)

    wt = Path(create_worktree(str(project), "task_bounded", "Implement feature X"))

    if commit_work:
        (wt / "feature.py").write_text("def feature():\n    return 42\n")
        _git("git", "add", "-A", cwd=wt)
        _git("git", "commit", "-qm", "coder: apply changes", cwd=wt)

    base_sha = _git("git", "rev-parse", "main", cwd=project).stdout.strip()
    return str(project), str(wt), base_sha


def _passing_validators(task, validators, shell_mcp, current_mode="", **kwargs):
    return [
        ValidatorResult(validator_id=v.validator_id, severity="pass",
                        justification="ok")
        for v in validators
    ]


def _state(task_id="task_bounded", spec="Implement feature X"):
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


def _bounded_adapter(wt: str, outcome) -> AGYAdapter:
    """An agy adapter whose run hits the given bound without returning."""
    adapter = AGYAdapter(workspace=Path(wt))

    def fake_run(*args, **kwargs):
        if outcome is CoderTimeoutError:
            raise subprocess.TimeoutExpired(
                cmd=["agy"], timeout=1800, output="Let me set up my plan", stderr="",
            )
        raise outcome("Coder exhausted its turn budget (20 turns) without submitting files")

    adapter._run_subprocess = fake_run  # type: ignore[method-assign]
    return adapter


def _builder(protocol, wt: str, coder, validator_fn, audit):
    # A per-worktree token store: the default store is shared per process, and
    # identical task ids issued in the same second mint identical single-use
    # tokens — a second graph in one test would see the first's token as
    # already consumed (Fixes #282 test setup).
    return GraphBuilder(
        protocol,
        workspace_mcp=WorkspaceMCP(wt),
        git_mcp=GitMCP(wt),
        shell_mcp=None,
        coder=coder,
        worktree_path=wt,
        validator_fn=validator_fn,
        token_issuer=TokenIssuer(
            secret=TEST_SECRET, ttl_seconds=3600,
            store_path=Path(wt).parent / ".tokens.db",
        ),
        audit_log=audit,
    )


@pytest.mark.parametrize("outcome", BOUNDED_OUTCOMES)
def test_bounded_run_with_branch_work_is_post_validated(
    protocol_with_post_execute, tmp_path, capsys, outcome
):
    """A run that hits either bound, with its work committed on the task branch,
    has that work judged rather than discarded as artifacts_count 0."""
    _, wt, base_sha = _task_worktree(tmp_path, commit_work=True)

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
    coder = _bounded_adapter(wt, outcome)
    builder = _builder(protocol_with_post_execute, wt, coder, capturing_validator, audit)

    result = builder.build_graph().compile().invoke(_state())

    # The produced work was not discarded: the judges saw it and passed it.
    assert result["is_blocked"] is False, outcome
    assert result["halt_type"] is None, outcome
    assert result["metadata"]["post_validation"]["outcome"] == "passed", outcome
    assert "feature.py" in result["artifacts"], outcome

    # Post-execute validation ran against the committed work, anchored at the
    # branch base so a diffing judge reviews base_ref..HEAD.
    assert len(post_calls) == 1, outcome
    assert "feature.py" in post_calls[0]["artifacts"], outcome
    assert post_calls[0]["base_ref"] == base_sha, outcome

    # The bound itself is still recorded: a run that hit a limit is worth
    # knowing about even when its work passes.
    payload = result["metadata"]["halt_payload"]
    assert payload["artifacts_count"] == 1, outcome
    if outcome is CoderTimeoutError:
        assert payload["timed_out"] is True
        assert payload["timeout_seconds"] == 1800
    else:
        assert payload["turn_budget_exhausted"] is True

    # The existing work-recovery path fired and said so.
    captured = capsys.readouterr().out
    assert "Work already present on the task branch" in captured, outcome
    ops = [call.args[0] for call in audit.append_event.call_args_list]
    assert "work_already_present" in ops, outcome
    assert "no_file_operations" not in ops, outcome


@pytest.mark.parametrize("outcome", BOUNDED_OUTCOMES)
def test_bounded_run_with_no_work_is_not_a_code_finding(
    protocol_with_post_execute, tmp_path, outcome
):
    """Either bound with no work anywhere is an operational halt, never a
    blocker verdict about code no judge faulted."""
    _, wt, _ = _task_worktree(tmp_path, commit_work=False)

    audit = MagicMock()
    coder = _bounded_adapter(wt, outcome)
    builder = _builder(protocol_with_post_execute, wt, coder, _passing_validators, audit)

    result = builder.build_graph().compile().invoke(_state())

    assert result["is_blocked"] is True, outcome
    # The raw bound survives on the state/closure; the canonical outcome is the
    # operational halt, never a verdict about the task.
    expected_raw = "environment_error" if outcome is CoderTimeoutError else "turn_budget_exhausted"
    assert result["halt_type"] == expected_raw, outcome

    payload = result["metadata"]["halt_payload"]
    assert payload["halt_type"] == "environment_error", outcome
    assert payload["final_decision"] == "environment_error", outcome
    assert payload["final_decision"] != "blocker", outcome

    # Post-validation was skipped (nothing to validate).
    assert payload["post_validation"]["outcome"] == "skipped", outcome

    # Recovery is not spawned against a run that produced nothing.
    assert result["spawned_subtasks"] == [], outcome


def test_the_two_bounded_outcomes_cannot_drift_apart(
    protocol_with_post_execute, tmp_path
):
    """The clock bound and the turn bound resolve to the same canonical halt and
    the same post-validation behaviour, so a future change cannot fix one and
    leave the other behind."""
    canonical = {}
    post_validated = {}

    for outcome in BOUNDED_OUTCOMES:
        # With recoverable work: post-validated, operational.
        _, wt_work, _ = _task_worktree(tmp_path / f"work_{outcome.__name__}", commit_work=True)
        builder = _builder(
            protocol_with_post_execute, wt_work, _bounded_adapter(wt_work, outcome),
            _passing_validators, MagicMock(),
        )
        with_work = builder.build_graph().compile().invoke(_state())

        # With no work: operational halt, not a blocker.
        _, wt_empty, _ = _task_worktree(tmp_path / f"empty_{outcome.__name__}", commit_work=False)
        builder = _builder(
            protocol_with_post_execute, wt_empty, _bounded_adapter(wt_empty, outcome),
            _passing_validators, MagicMock(),
        )
        without_work = builder.build_graph().compile().invoke(_state())

        post_validated[outcome] = (
            with_work["is_blocked"] is False
            and with_work["metadata"]["post_validation"]["outcome"] == "passed"
        )
        canonical[outcome] = without_work["metadata"]["halt_payload"]["final_decision"]

    # One bound is not "fixed" while the other is still a code verdict:
    # identical behaviour and identical canonical outcome for both.
    assert len(set(canonical.values())) == 1, canonical
    assert canonical[CoderTimeoutError] == "environment_error"
    assert post_validated[CoderTimeoutError] is True
    assert post_validated[TurnBudgetExhausted] is True
