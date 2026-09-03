"""A coder backend that rejects the invocation is a config-fixable halt, not an engine error.

FILE: tests/engine/test_coder_invocation_failure.py (Fixes #195)

The motivating case: an operator passes a model string the coder's CLI does not
accept. The run used to halt with ``internal_error`` and a hint telling the
operator to inspect engine logs — nothing failed internally, and the reason
field already held the exact answer. A coder backend that cannot be started, a
binary that is missing, or arguments it rejects is an operator-fixable coder
fault: it must halt under the raw ``execution_error`` (canonical ``blocker``)
with a config fix target, and recovery must not spawn against it.
"""

import subprocess
from unittest import mock

import pytest
from snodo.coders.agy_adapter import AGYAdapter
from snodo.coders.base import LLMCallError
from snodo.compiler.models import DisagreementPolicy, Mode, Protocol, Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.engine.closure import run_to_closure
from snodo.engine.loop import build_protocol_graph


@pytest.fixture
def git_fixture_repo(tmp_path):
    """A throwaway git repo with an initial commit, for graph execution."""
    root = tmp_path / "fixture"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    return root


def _protocol():
    return Protocol(
        protocol_id="coder-invocation",
        name="Coder Invocation",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit"],
                validators=["v1"],
            )
        ],
        validators=[
            Validator(validator_id="v1", validator_type="security",
                      criteria=["ok"]),
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer",
    )


def _passing_validators(task, validators, shell, **kwargs):
    return [
        ValidatorResult(validator_id=v.validator_id, severity="pass",
                        justification="ok")
        for v in validators
    ]


def _run_with_coder(coder, git_fixture_repo):
    graph = build_protocol_graph(
        _protocol(),
        project_root=str(git_fixture_repo),
        use_mock_coder=False,
        coder=coder,
        validator_fn=_passing_validators,
    ).compile()
    task = Task(id="task_invocation", spec="do the thing")
    _final, tree = run_to_closure(graph, task, mode="producer")
    return tree


def test_cli_rejects_model_is_execution_error_not_internal_error(git_fixture_repo):
    """A model string the coder's CLI rejects halts as a config-fixable blocker.

    This is the exact real-run case: ``agy run failed (rc=1): invalid model
    selection``. The reason field already holds the answer; the taxonomy and
    the hint must agree with it (Fixes #195).
    """
    def fake_popen(argv, **kwargs):
        proc = mock.MagicMock()
        proc.pid = 12345
        proc.returncode = 1
        proc.communicate.return_value = (
            "",
            'Error: invalid model selection (--model "gemini-3.7-flash")',
        )
        return proc

    coder = AGYAdapter(model="agy/gemini-3.7-flash", workspace=git_fixture_repo)
    with mock.patch("subprocess.Popen", side_effect=fake_popen):
        tree = _run_with_coder(coder, git_fixture_repo)

    # Raw halt names the coder fault; canonical outcome is a blocker.
    assert tree.outcome == "execution_error"
    assert tree.outcome != "internal_error"

    payload = tree.halt_payload
    assert payload is not None
    assert payload["status"] == "blocked"
    assert payload["halt_type"] == "blocker"
    assert payload["final_decision"] == "blocker"
    assert payload["raw_halt_type"] == "blocker"
    assert payload["artifacts_count"] == 0

    # The exact coder error reaches the top-level reason.
    assert "invalid model selection" in (payload["reason"] or "")

    # The hint tells the operator to fix the coder configuration, not to
    # inspect engine logs.
    assert "coder configuration" in payload["hint"]
    assert "internal" not in payload["hint"]
    assert "inspect the logs" not in payload["hint"]

    # Post-validation was skipped (nothing to validate).
    assert payload["post_validation"]["outcome"] == "skipped"

    # Recovery must not spawn against a coder invocation failure.
    assert tree.spawned_subtasks == 0
    assert tree.subtasks == []


def test_missing_binary_is_execution_error(git_fixture_repo):
    """A coder binary missing from PATH is a config-fixable halt, not an engine error."""
    coder = AGYAdapter(workspace=git_fixture_repo)
    with mock.patch("subprocess.Popen", side_effect=FileNotFoundError):
        tree = _run_with_coder(coder, git_fixture_repo)

    assert tree.outcome == "execution_error"
    payload = tree.halt_payload
    assert payload["halt_type"] == "blocker"
    assert payload["raw_halt_type"] == "blocker"
    assert "agy not found on PATH" in (payload["reason"] or "")
    assert "coder configuration" in payload["hint"]


def test_llm_call_error_is_execution_error(git_fixture_repo):
    """An LLM call failure (LLMCallError) is a coder fault, not an engine fault."""
    def failing_executor(task, token, coder, workspace_mcp, git_mcp, **kwargs):
        raise LLMCallError("LLM call failed: provider returned 401")

    graph = build_protocol_graph(
        _protocol(),
        project_root=str(git_fixture_repo),
        use_mock_coder=False,
        coder=AGYAdapter(workspace=git_fixture_repo),
        executor_fn=failing_executor,
        validator_fn=_passing_validators,
    ).compile()
    task = Task(id="task_llm", spec="do the thing")
    _final, tree = run_to_closure(graph, task, mode="producer")

    assert tree.outcome == "execution_error"
    payload = tree.halt_payload
    assert payload["halt_type"] == "blocker"
    assert payload["raw_halt_type"] == "blocker"
    assert "401" in (payload["reason"] or "")
