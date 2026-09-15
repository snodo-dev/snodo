"""Turn-budget exhaustion is a nameable operational halt, not a code verdict.

FILE: tests/engine/test_turn_budget_exhaustion.py

When a coder burns its full tool-loop turn budget without submitting files,
the run must halt under a distinct raw outcome (``turn_budget_exhausted``) that
resolves to the operational ``environment_error`` — a fact about the run, not a
verdict about code no judge saw — and must NOT spawn a recovery subtask.
Retrying a turn-budget exhaustion cannot converge, so the recovery ladder has
nothing to learn from another attempt (Fixes #282).
"""

import json
import subprocess
from unittest.mock import MagicMock

import pytest
from snodo.coders.litellm import LiteLLMAdapter
from snodo.compiler.models import Mode, Protocol, Validator
from snodo.core.interfaces import Task
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


@pytest.fixture
def solo_protocol():
    return Protocol(
        protocol_id="test_turn_budget",
        name="Test Turn Budget Protocol",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit"],
                validators=["spec_check", "quality"],
            )
        ],
        validators=[
            Validator(
                validator_id="spec_check",
                validator_type="llm",
                criteria=["Spec is clear"],
            ),
            Validator(
                validator_id="quality",
                validator_type="quality",
                criteria=["Code must compile"],
            ),
        ],
        initial_mode="producer",
    )


def _read_file_call_response(*args, **kwargs):
    """A mock response that always issues a read_file tool call (never submits)."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = None
    tc = MagicMock()
    tc.id = "tc_read"
    tc.function.name = "read_file"
    tc.function.arguments = json.dumps({"path": "README.md"})
    response.choices[0].message.tool_calls = [tc]
    response.choices[0].finish_reason = "tool_calls"
    return response


def _passing_validators(task, validators, shell, **kwargs):
    return [
        MagicMock(validator_id=v.validator_id, severity="pass",
                  justification="ok", error=False)
        for v in validators
    ]


def test_turn_budget_exhaustion_is_an_operational_halt_not_internal_error(
    solo_protocol, git_fixture_repo,
):
    """Coder exhausts its turn budget -> raw halt ``turn_budget_exhausted``,
    canonical ``environment_error`` (the operational family, ADR 015), zero
    artifacts, skipped post-validation, and no recovery subtask."""
    adapter = LiteLLMAdapter(model="gpt-4o", max_tool_turns=3)
    adapter._completion_fn = MagicMock(side_effect=_read_file_call_response)

    graph = build_protocol_graph(
        protocol=solo_protocol,
        project_root=str(git_fixture_repo),
        use_mock_coder=False,
        coder=adapter,
        validator_fn=_passing_validators,
    ).compile()

    task = Task(id="task_turn_budget", spec="Huge task")
    _final_state, tree = run_to_closure(graph, task, mode="producer")

    # The specific raw outcome survives on the closure tree, not laundered into
    # ``internal_error``.
    assert tree.outcome == "turn_budget_exhausted"
    assert tree.outcome != "internal_error"

    payload = tree.halt_payload
    assert payload is not None
    assert payload["status"] == "blocked"
    # The raw cause is ``turn_budget_exhausted``; the canonical outcome is the
    # operational ``environment_error`` — a fact about the run, NOT a blocker
    # verdict about code no judge saw (Fixes #282).
    assert payload["raw_halt_type"] == "environment_error"
    assert payload["halt_type"] == "environment_error"
    assert payload["final_decision"] == "environment_error"
    assert payload["final_decision"] != "blocker"
    assert payload["artifacts_count"] == 0

    # The turn budget is named in the reason so the operator can tell this
    # apart from an ordinary blocker or a crash.
    assert "turn budget" in (payload["reason"] or "")

    # The hint points at the run's own turn bound, not at the code, the spec or
    # an install.
    hint = payload["hint"]
    assert "turn budget" in hint
    assert "install the program" not in hint
    assert "Fix the produced code" not in hint

    # Post-validation was skipped (nothing to validate).
    assert payload["post_validation"] is not None
    assert payload["post_validation"]["outcome"] == "skipped"

    # Recovery must not spawn for a turn-budget exhaustion.
    assert tree.spawned_subtasks == 0
    assert tree.subtasks == []
