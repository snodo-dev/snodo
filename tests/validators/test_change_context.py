"""The produced change reaches post-execute judges because of the PHASE.

FILE: tests/validators/test_change_context.py (Fixes #267)

An acceptance judge that must reconstruct "what did the coder do" by reading
the repository burns its budget rediscovering the change; an observed judge
ran 41, 49, and 44 turns to no verdict, then passed at turn 26 only because it
happened to open the three changed files early.  The engine already knows
what changed (base_ref..HEAD on the task branch); this suite pins that a
post-execute judge's context includes the change — with or without tool
grants, in the tool loop and on the single-completion path — and that a
pre-execute judge's never does.

The two phases are distinguished by ``evaluation_phase`` and nothing else:
every test here runs a validator that is byte-identical (same id, type,
criteria, tools, model, artifacts, base_ref) across both phases, so an
implementation keying on id, name, type, tool grants, or artifact presence
cannot pass both directions.
"""

import json
from unittest.mock import MagicMock

import pytest
from snodo.compiler.models import Validator
from snodo.core.interfaces import Task
from snodo.validators.runner import run_validators

BASE_REF = "b" * 40
CHANGE_DIFF = (
    "diff --git a/src/cart.py b/src/cart.py\n"
    "@@ -1,3 +1,5 @@\n"
    "+def add_item(cart, item):\n"
    "+    cart.append(item)\n"
    "diff --git a/tests/test_cart.py b/tests/test_cart.py\n"
    "@@ -0,0 +1,2 @@\n"
    "+def test_add_item():\n"
    "+    assert add_item([], 1) == [1]\n"
)
ARTIFACTS = ["src/cart.py", "tests/test_cart.py"]


def _protocol():
    protocol = MagicMock()
    protocol.get_mode.return_value = None
    return protocol


def _validator(phase, tools):
    """One judge spec; across phases every other field is identical."""
    return Validator(
        validator_id="acc",
        validator_type="acceptance",
        evaluation_phase=phase,
        criteria=[
            "Judge the produced artifacts against the acceptance criteria "
            "in the task spec."
        ],
        tools=list(tools),
    )


def _tool_loop_completion():
    """A judge that submits its verdict on its very first turn."""
    calls = []

    def fn(**kwargs):
        calls.append(kwargs)
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = None
        tc = MagicMock()
        tc.id = "tc_verdict"
        tc.function.name = "submit_verdict"
        tc.function.arguments = json.dumps({
            "severity": "pass",
            "justification": "criteria satisfied from the provided change",
        })
        resp.choices[0].message.tool_calls = [tc]
        return resp

    fn.calls = calls
    return fn


def _single_completion():
    calls = []

    def fn(**kwargs):
        calls.append(kwargs)
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = json.dumps({
            "severity": "pass",
            "justification": "criteria satisfied",
        })
        resp.choices[0].message.tool_calls = []
        return resp

    fn.calls = calls
    return fn


def _first_prompt(fn):
    for kwargs in fn.calls:
        messages = kwargs.get("messages") or []
        if messages:
            return messages[0]["content"]
    raise AssertionError("judge was never called with a prompt")


def _run(phase, tools, completion, mock_git, mock_workspace,
         validators=None, base_ref=BASE_REF, artifacts=None):
    results, _caps = run_validators(
        protocol=_protocol(),
        validators=validators or [_validator(phase, tools)],
        task=Task(id="t1", spec="Add cart items.\n\nDONE WHEN:\n1. add_item exists\n2. it is tested"),
        phase=phase,
        completion_fn=completion,
        default_model="gpt-4",
        validator_config=MagicMock(max_tokens=1500, max_tool_turns=20),
        workspace_mcp=mock_workspace,
        git_mcp=mock_git,
        current_mode="build",
        artifacts=list(artifacts if artifacts is not None else ARTIFACTS),
        base_ref=base_ref,
    )
    return results


def _git(diff=CHANGE_DIFF, raises=False):
    mock = MagicMock()
    if raises:
        mock.diff_between_refs.side_effect = Exception("bad revision")
    else:
        mock.diff_between_refs.return_value = diff
    return mock


class TestPhaseIsTheOnlyDiscriminator:
    """The same judge, identical except evaluation_phase: the change rides
    with post-execute and never with pre-execute."""

    @pytest.mark.parametrize("tools", [
        pytest.param(["read_file", "list_files"], id="no-diff-grant"),
        pytest.param(["read_file", "list_files", "read_diff_between_refs"],
                     id="diff-granted"),
    ])
    def test_tool_loop_judge(self, tools):
        completion = _tool_loop_completion()
        _run("post_execute", tools, completion, _git(), MagicMock())
        prompt = _first_prompt(completion)
        assert "## Code Change" in prompt
        assert CHANGE_DIFF in prompt
        assert "src/cart.py" in prompt

        completion = _tool_loop_completion()
        _run("pre_execute", tools, completion, _git(), MagicMock())
        prompt = _first_prompt(completion)
        assert "## Code Change" not in prompt
        assert "diff --git" not in prompt
        assert "Files Changed" not in prompt

    def test_single_completion_judge_has_no_tools_to_grant(self):
        """A post-execute judge with no declared tools never enters a tool
        loop — a guaranteed tool grant could not reach it.  The prompt can."""
        completion = _single_completion()
        _run("post_execute", [], completion, _git(), MagicMock())
        prompt = _first_prompt(completion)
        assert "## Code Change" in prompt
        assert "src/cart.py" in prompt

        completion = _single_completion()
        _run("pre_execute", [], completion, _git(), MagicMock())
        prompt = _first_prompt(completion)
        assert "## Code Change" not in prompt
        assert "diff --git" not in prompt

    def test_pre_execute_is_clean_even_with_artifacts_and_anchor(self):
        """Reconstruction keying on artifact presence, a base_ref, or the
        judge's type — the tempting non-phase signals — must not leak a diff
        into a proposal review.  Pre-execute runs here carry the same
        artifacts and the same base_ref as the post-execute run above."""
        completion = _tool_loop_completion()
        _run("pre_execute", ["read_file", "list_files"], completion, _git(),
             MagicMock(), base_ref=BASE_REF, artifacts=ARTIFACTS)
        prompt = _first_prompt(completion)
        assert "## Code Change" not in prompt
        assert "Files Changed" not in prompt

    def test_mode_transition_is_not_post_execute(self):
        completion = _single_completion()
        _run("mode_transition", ["read_file"], completion, _git(), MagicMock())
        prompt = _first_prompt(completion)
        assert "## Code Change" not in prompt


class TestChangeReachesTheJudge:
    def test_post_execute_judge_can_state_files_changed_at_turn_zero(self):
        """The judge answers with no tool calls at all: everything it needs
        to name the changed files was already in its first prompt."""
        completion = _tool_loop_completion()
        results = _run("post_execute", ["read_file", "list_files"],
                       completion, _git(), MagicMock())
        assert len(completion.calls) == 1
        assert results[0].severity == "pass"
        prompt = _first_prompt(completion)
        assert "add_item" in prompt  # the change content, not just paths
        assert "The engine already established this change for you" in prompt

    def test_change_is_read_once_per_pass_not_once_per_judge(self):
        mock_git = _git()
        validators = [
            Validator(
                validator_id=f"post-{i}",
                validator_type="architecture",
                evaluation_phase="post_execute",
                criteria=["Do the architecture thing."],
                tools=["read_file"],
            )
            for i in range(3)
        ]
        completion = _tool_loop_completion()
        run_validators(
            protocol=_protocol(),
            validators=validators,
            task=Task(id="t1", spec="Build"),
            phase="post_execute",
            completion_fn=completion,
            default_model="gpt-4",
            validator_config=MagicMock(max_tokens=1500, max_tool_turns=20),
            workspace_mcp=MagicMock(),
            git_mcp=mock_git,
            current_mode="build",
            artifacts=list(ARTIFACTS),
            base_ref=BASE_REF,
        )
        assert mock_git.diff_between_refs.call_count == 1

    def test_fallback_range_is_labeled_as_such_without_anchor(self):
        completion = _single_completion()
        _run("post_execute", [], completion, _git(), MagicMock(), base_ref=None)
        prompt = _first_prompt(completion)
        assert "## Code Change (HEAD~1..HEAD)" in prompt
        assert "no execute-node HEAD anchor was available" in prompt

    def test_unreadable_range_is_not_reported_as_no_change(self):
        completion = _single_completion()
        _run("post_execute", [], completion, _git(raises=True), MagicMock())
        prompt = _first_prompt(completion)
        assert "(unable to read diff" in prompt
        assert "records no changes" not in prompt

    def test_empty_range_says_the_change_is_empty(self):
        completion = _single_completion()
        _run("post_execute", [], completion, _git(diff=""), MagicMock())
        prompt = _first_prompt(completion)
        assert f"## Code Change ({BASE_REF}..HEAD)" in prompt
        assert "records no changes" in prompt

    def test_no_git_view_falls_back_to_engine_recorded_files(self):
        completion = _single_completion()
        _run("post_execute", [], completion, None, MagicMock())
        prompt = _first_prompt(completion)
        assert "## Files Changed (engine-recorded)" in prompt
        for artifact in ARTIFACTS:
            assert artifact in prompt


class TestGrantsStillGovernCapabilities:
    """The change is context, not a capability: what the judge may CALL is
    still exactly what the protocol granted."""

    def test_ungranted_diff_tool_is_not_offered_but_change_is_shown(self):
        completion = _tool_loop_completion()
        _run("post_execute", ["read_file"], completion, _git(), MagicMock())
        offered = {
            t["function"]["name"]
            for t in completion.calls[0]["tools"]
        }
        assert "read_diff_between_refs" not in offered
        assert "read_file" in offered
        prompt = _first_prompt(completion)
        assert "## Code Change" in prompt

    def test_granted_diff_tool_remains_offered(self):
        completion = _tool_loop_completion()
        _run("post_execute", ["read_file", "read_diff_between_refs"],
             completion, _git(), MagicMock())
        offered = {
            t["function"]["name"]
            for t in completion.calls[0]["tools"]
        }
        assert "read_diff_between_refs" in offered

    def test_pre_execute_diff_grant_remains_stripped(self):
        completion = _tool_loop_completion()
        _run("pre_execute", ["read_file", "read_diff_between_refs"],
             completion, _git(), MagicMock())
        offered = {
            t["function"]["name"]
            for t in completion.calls[0]["tools"]
        }
        assert "read_diff_between_refs" not in offered
        assert completion.calls[0].get("messages"), "judge should still run"
        mock_git = _git()
        _run("pre_execute", ["read_file", "read_diff_between_refs"],
             _tool_loop_completion(), mock_git, MagicMock())
        mock_git.diff_between_refs.assert_not_called()
