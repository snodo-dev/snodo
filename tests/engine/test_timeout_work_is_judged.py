"""A coder run that ends on the clock has its produced work judged.

FILE: tests/engine/test_timeout_work_is_judged.py (Fixes #281)

PROVES:
- A run that completes its work and then times out has that work carried into
  post-execute validation rather than discarded: if the work is already
  committed on the task branch (an earlier attempt, or this run's own commit
  that the adapter's readback missed), the existing work-recovery probe fires
  and the judges decide. The timeout is still recorded.
- A timed-out run whose tree is unchanged is not reported as a code finding: it
  halts under the operational ``environment_error`` (ADR 015), never as a
  blocker verdict about work no judge faulted, with a hint that names the
  timeout rather than an install or a coder-config fix.
- Every field in a halt payload comes from the run the payload describes: the
  builder's per-run scratch facts cannot leak a previous attempt's output.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from snodo.coders.agy_adapter import AGYAdapter
from snodo.compiler.models import DisagreementPolicy, Mode, Protocol, Validator
from snodo.core.interfaces import ValidatorResult
from snodo.engine.loop import GraphBuilder
from snodo.infrastructure.tokens import TokenIssuer
from snodo.infrastructure.worktree import create_worktree
from snodo.tools.git import GitMCP
from snodo.tools.workspace import WorkspaceMCP

from tests.conftest import TEST_SECRET


@pytest.fixture
def protocol_with_post_execute():
    return Protocol(
        protocol_id="timeout_work",
        name="Timeout Work",
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
    project.mkdir()
    _init_repo(project)

    wt = Path(create_worktree(str(project), "task_timeout", "Implement feature X"))

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


def _state(task_id="task_timeout", spec="Implement feature X"):
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


def _timing_out_adapter(wt: str, tail: str = "Let me set up my plan") -> AGYAdapter:
    """An agy adapter whose run hangs until the clock kills it."""
    adapter = AGYAdapter(workspace=Path(wt))

    def fake_run_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=["agy"], timeout=1800, output=tail, stderr="",
        )

    adapter._run_subprocess = fake_run_timeout  # type: ignore[method-assign]
    return adapter


def _builder(protocol, wt: str, coder, validator_fn, audit):
    return GraphBuilder(
        protocol,
        workspace_mcp=WorkspaceMCP(wt),
        git_mcp=GitMCP(wt),
        shell_mcp=None,
        coder=coder,
        worktree_path=wt,
        validator_fn=validator_fn,
        token_issuer=TokenIssuer(secret=TEST_SECRET, ttl_seconds=3600),
        audit_log=audit,
    )


def test_timed_out_run_with_branch_work_is_post_validated_not_discarded(
    protocol_with_post_execute, tmp_path, capsys
):
    """A timeout whose work is already committed on the task branch is judged,
    not thrown away as artifacts_count 0."""
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
    coder = _timing_out_adapter(wt)
    builder = _builder(protocol_with_post_execute, wt, coder, capturing_validator, audit)

    result = builder.build_graph().compile().invoke(_state())

    # The produced work was not discarded: the judges saw it and passed it.
    assert result["is_blocked"] is False
    assert result["halt_type"] is None
    assert result["metadata"]["post_validation"]["outcome"] == "passed"
    assert "feature.py" in result["artifacts"]

    # Post-execute validation ran against the committed work, anchored at the
    # branch base so a diffing judge reviews base_ref..HEAD.
    assert len(post_calls) == 1
    assert "feature.py" in post_calls[0]["artifacts"]
    assert post_calls[0]["base_ref"] == base_sha

    # The timeout itself is still recorded: a run that ran out of time is worth
    # knowing about even when its work passes.
    payload = result["metadata"]["halt_payload"]
    assert payload["timed_out"] is True
    assert payload["timeout_seconds"] == 1800
    assert payload["artifacts_count"] == 1

    # The existing work-recovery path fired and said so.
    captured = capsys.readouterr().out
    assert "Work already present on the task branch" in captured
    ops = [call.args[0] for call in audit.append_event.call_args_list]
    assert "work_already_present" in ops
    assert "no_file_operations" not in ops


def test_timed_out_run_with_unchanged_tree_is_not_a_code_finding(
    protocol_with_post_execute, tmp_path
):
    """A timeout with no work anywhere is an operational halt, never a blocker
    verdict about code no judge faulted."""
    _, wt, _ = _task_worktree(tmp_path, commit_work=False)

    audit = MagicMock()
    coder = _timing_out_adapter(wt)
    builder = _builder(protocol_with_post_execute, wt, coder, _passing_validators, audit)

    result = builder.build_graph().compile().invoke(_state())

    assert result["is_blocked"] is True
    assert result["halt_type"] == "environment_error"

    payload = result["metadata"]["halt_payload"]
    # Raw and canonical are both the operational outcome; it is no one's
    # verdict about the task.
    assert payload["halt_type"] == "environment_error"
    assert payload["final_decision"] == "environment_error"
    assert payload["raw_halt_type"] == "environment_error"
    assert payload["final_decision"] != "blocker"

    # The timeout is recorded, with the run's own ending.
    assert payload["timed_out"] is True
    assert payload["timeout_seconds"] == 1800
    assert "timed out" in (payload["reason"] or "")
    assert "Let me set up my plan" in (payload["output_tail"] or "")

    # Post-validation was skipped (nothing to validate).
    assert payload["post_validation"]["outcome"] == "skipped"

    # The hint points at what actually happened — a time budget, not an install
    # or a coder-configuration fix.
    hint = payload["hint"]
    assert "time budget" in hint
    assert "install the program" not in hint
    assert "coder configuration" not in hint

    # Recovery is not spawned against a run that produced nothing.
    assert result["spawned_subtasks"] == []


def test_halt_payload_cannot_carry_previous_attempt_output(
    protocol_with_post_execute, tmp_path
):
    """The builder's per-run scratch facts must not leak into a new run's
    payload: a field a human reads to understand a halt comes from the run the
    payload describes."""
    _, wt, _ = _task_worktree(tmp_path, commit_work=False)

    builder = _builder(
        protocol_with_post_execute, wt, _timing_out_adapter(wt),
        _passing_validators, MagicMock(),
    )

    # Simulate the scratch facts left behind by an earlier timed-out attempt.
    builder._last_timed_out = True
    builder._last_timeout_seconds = 3600
    builder._last_timeout_tail = "EARLIER ATTEMPT: Let me set up my plan"
    builder._last_output_tail = "EARLIER ATTEMPT: Let me set up my plan"

    # A new run that blocked for its own reason and left no output tail.
    state = _state()
    state["is_blocked"] = True
    state["halt_type"] = "blocked"
    state["constraint_violations"] = ["a judge found the code wanting"]
    loop_state = builder._dict_to_state(state)

    payload = builder._build_halt_payload(loop_state)

    assert "EARLIER ATTEMPT" not in (payload.get("output_tail") or "")
    assert "EARLIER ATTEMPT" not in (payload.get("reason") or "")
    assert "timed_out" not in payload
    assert "timeout_seconds" not in payload
