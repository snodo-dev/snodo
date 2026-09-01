"""Turn-budget exhaustion is a nameable halt, not an internal error.

FILE: tests/engine/test_turn_budget_exhaustion.py

When a coder burns its full tool-loop turn budget without submitting files,
the run must halt under a distinct, anticipated outcome — canonical ``blocker``,
raw ``turn_budget_exhausted`` — and must NOT spawn a recovery subtask. Retrying
a turn-budget exhaustion cannot converge, so the recovery ladder has nothing to
learn from another attempt.
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


def test_turn_budget_exhaustion_is_a_blocker_not_internal_error(
    solo_protocol, git_fixture_repo,
):
    """Coder exhausts its turn budget -> raw halt ``turn_budget_exhausted``,
    canonical ``blocker``, zero artifacts, skipped post-validation, and no
    recovery subtask."""
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

    # Distinct outcome, not the internal_error it used to be reported as.
    assert tree.outcome == "turn_budget_exhausted"
    assert tree.outcome != "internal_error"

    payload = tree.halt_payload
    assert payload is not None
    assert payload["status"] == "blocked"
    # Canonical four-outcome vocabulary (a blocker), with the specific reason
    # carried in ``reason`` and the closure tree's raw ``turn_budget_exhausted``.
    assert payload["halt_type"] == "blocker"
    assert payload["final_decision"] == "blocker"
    assert payload["raw_halt_type"] == "blocker"
    assert payload["artifacts_count"] == 0

    # The turn budget is named in the reason so the operator can tell this
    # apart from an ordinary blocker or a crash.
    assert "turn budget" in (payload["reason"] or "")

    # Post-validation was skipped (nothing to validate).
    assert payload["post_validation"] is not None
    assert payload["post_validation"]["outcome"] == "skipped"

    # Recovery must not spawn for a turn-budget exhaustion.
    assert tree.spawned_subtasks == 0
    assert tree.subtasks == []
