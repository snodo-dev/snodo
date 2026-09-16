"""A coder backend that rejects the invocation is a config-fixable halt, not an engine error.

FILE: tests/engine/test_coder_invocation_failure.py (Fixes #195)

The motivating case: an operator passes a model string the coder's CLI does not
accept. The run used to halt with ``internal_error`` and a hint telling the
operator to inspect engine logs — nothing failed internally, and the reason
field already held the exact answer. A coder backend that rejects its
arguments is an operator-fixable coder fault: it halts under the raw
``execution_error`` (canonical ``blocker``, config fix target).

A program that is NOT INSTALLED is a different fault and does not join that
family: nothing about the task is blocking. A missing coder binary (or
container runtime) halts under ``environment_error`` — raw AND canonical —
with the install command in the operator-facing message, and it produces no
blocker verdict and no recovery against the unchanged spec.
"""

import io
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
        proc.stdout = io.StringIO("")
        proc.stderr = io.StringIO(
            'Error: invalid model selection (--model "gemini-3.7-flash")'
        )
        return proc

    coder = AGYAdapter(model="agy/gemini-3.7-flash", workspace=git_fixture_repo)
    with mock.patch("subprocess.Popen", side_effect=fake_popen):
        tree = _run_with_coder(coder, git_fixture_repo)

    # Raw halt names the coder fault; canonical outcome is an operational halt.
    assert tree.outcome == "execution_error"
    assert tree.outcome != "internal_error"

    payload = tree.halt_payload
    assert payload is not None
    assert payload["status"] == "blocked"
    assert payload["halt_type"] == "environment_error"
    assert payload["final_decision"] == "environment_error"
    assert payload["raw_halt_type"] == "environment_error"
    assert payload["artifacts_count"] == 0

    # The exact coder error reaches the top-level reason.
    assert "invalid model selection" in (payload["reason"] or "")

    # The hint names the real cause, not coder configuration (Fixes #301).
    assert "invalid model selection" in payload["hint"]
    assert "coder configuration" not in payload["hint"]
    assert "internal" not in payload["hint"]
    assert "inspect the logs" not in payload["hint"]

    # Post-validation was skipped (nothing to validate).
    assert payload["post_validation"]["outcome"] == "skipped"

    # Recovery must not spawn against a coder invocation failure.
    assert tree.spawned_subtasks == 0
    assert tree.subtasks == []


def test_missing_binary_is_environment_error_not_a_verdict(git_fixture_repo):
    """A coder binary absent from PATH halts as an environment fault.

    The distinction the taxonomy must keep: the SAME graph, a coder whose
    binary exists but rejects the arguments, halts as a config-fixable
    blocker (test above); a coder whose binary is not installed must NOT —
    its outcome is not one of the four verdicts, the operator message carries
    the install command, no recovery spawns against the unchanged
    specification, and the recorded outcome does not read as blocked.
    """
    coder = AGYAdapter(workspace=git_fixture_repo)
    with mock.patch("subprocess.Popen", side_effect=FileNotFoundError):
        tree = _run_with_coder(coder, git_fixture_repo)

    assert tree.outcome == "environment_error"

    payload = tree.halt_payload
    assert payload is not None
    # Raw and canonical are BOTH the environment outcome: it is no one's
    # verdict about the task.
    assert payload["halt_type"] == "environment_error"
    assert payload["final_decision"] == "environment_error"
    assert payload["raw_halt_type"] == "environment_error"
    assert payload["final_decision"] != "blocker"
    assert payload["artifacts_count"] == 0

    # The reason names the missing program; the hint carries the INSTALL
    # COMMAND — not a fix hint about a spec that passed every validator.
    assert "agy not found on PATH" in (payload["reason"] or "")
    assert "https://antigravity.google/docs/cli" in payload["hint"]
    assert "Revise the task spec" not in payload["hint"]
    assert "Fix the produced code" not in payload["hint"]

    # Post-validation was skipped (nothing to validate).
    assert payload["post_validation"]["outcome"] == "skipped"

    # Recovery is not spawned against an unchanged specification.
    assert tree.spawned_subtasks == 0
    assert tree.subtasks == []


def test_container_runtime_missing_is_environment_error(git_fixture_repo):
    """A coder whose container runtime cannot be reached is also an
    environment fault, not a coder-configuration blocker."""
    from snodo.coders.opencode_adapter import OpenCodeAdapter

    container = mock.MagicMock()
    container.is_running.return_value = False
    container.is_available.return_value = False
    coder = OpenCodeAdapter(workspace=git_fixture_repo, container=container)
    tree = _run_with_coder(coder, git_fixture_repo)

    assert tree.outcome == "environment_error"
    payload = tree.halt_payload
    assert payload["final_decision"] == "environment_error"
    assert payload["final_decision"] != "blocker"
    assert "docker" in (payload["reason"] or "").lower()
    assert "Docker" in payload["hint"]
    assert tree.spawned_subtasks == 0


def test_llm_call_error_is_execution_error(git_fixture_repo):
    """An LLM call failure (LLMCallError) is an operational fault, not a blocker verdict (Fixes #301)."""
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
    assert payload["halt_type"] == "environment_error"
    assert payload["raw_halt_type"] == "environment_error"
    assert payload["final_decision"] == "environment_error"
    assert "401" in (payload["reason"] or "")
    assert "401" in payload["hint"]
    assert "coder configuration" not in payload["hint"]
    assert not payload.get("retryable")
