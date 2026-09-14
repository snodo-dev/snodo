"""The produced change reaches post-execute judges because of the PHASE.

FILE: tests/validators/test_change_context.py (Fixes #267, #269)

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

The section is also bounded (Fixes #269): waves routinely regenerate a
lockfile or bundle whose diff runs to tens of thousands of lines, and the
injected section reaches every post-execute judge — including
single-completion ones with no turn budget to recover with.  The bound tests
pin that the section fits for a change of any size, says so when it shows
only part of the change, and names every changed file even when dropping
their contents.
"""

import json
from unittest.mock import MagicMock

import pytest
from snodo.compiler.models import Validator
from snodo.core.interfaces import Task
from snodo.validators.change import (
    CHANGE_SECTION_CHAR_LIMIT,
    ChangeContext,
    _fit_diff_body,
    render_change_block,
)
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


def _source_diff(path, marker, lines=30):
    """A git-diff-shaped chunk for a small hand-edited source file."""
    body = "\n".join(f"+feature work {i} " + "x" * 40 for i in range(lines))
    return (
        f"diff --git a/{path} b/{path}\n"
        f"index 0000000..1111111\n"
        f"--- /dev/null\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{lines + 1} @@\n"
        f"+# {marker}\n"
        f"{body}\n"
    )


def _bulk_diff(path, marker, lines=3000):
    """A regenerated-bulk file (lockfile, bundle): the sentinel marker rides
    at the END of a long chunk, deep enough that a bounded rendering cuts
    before it while the file's name must survive."""
    body = "\n".join(
        f'+resolved "https://registry.example/pkg{i}.tar.gz"' for i in range(lines)
    )
    return (
        f"diff --git a/{path} b/{path}\n"
        f"index 0000000..1111111\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -1,{lines} +1,{lines + 1} @@\n"
        f"{body}\n"
        f"-{marker}-old-version\n"
    )


def _binary_diff(path):
    """A header-only chunk: a binary file's diff has no ---/+++ lines, so its
    path can only come from the ``diff --git`` header."""
    return (
        f"diff --git a/{path} b/{path}\n"
        f"index 0000000..1111111\n"
        f"Binary files a/{path} and b/{path} differ\n"
    )


class TestChangeSectionIsBounded:
    """A change of any size yields a section within the stated bound; a
    truncated section says so and still names every changed file (#269)."""

    SOURCE_FILES = ["src/cart.py", "src/checkout.py", "tests/test_cart.py"]
    BULK_FILES = ["uv.lock", "dist/app.min.js"]

    def _huge_diff(self):
        diff = "".join(
            _source_diff(p, f"SOURCE-HUNK-{i}")
            for i, p in enumerate(self.SOURCE_FILES)
        )
        diff += "".join(
            _bulk_diff(p, f"BULK-{i}") for i, p in enumerate(self.BULK_FILES)
        )
        return diff

    def test_section_stays_within_the_stated_bound(self):
        diff = self._huge_diff()
        # _huge_diff() is ~315k chars; the new 64k limit means it is still
        # well over budget (≈5×), even though 10× no longer holds.
        assert len(diff) > 4 * CHANGE_SECTION_CHAR_LIMIT, "test must be far over budget"
        section = render_change_block(ChangeContext(label="abc123..HEAD", diff=diff))
        assert len(section) <= CHANGE_SECTION_CHAR_LIMIT

    def test_median_commit_for_this_repository_is_not_truncated(self):
        """A change of median size for this repository fits without truncation.

        Measured on the last 40 non-merge commits on main: median is 21,444
        characters (p50).  At the new 64,000-character limit, 95 % of commits
        (38/40) fit verbatim.  This test pins the common case: a commit near
        the median must never trigger the truncation path.
        """
        # Build a realistic median-sized diff: several source files totalling
        # ~21,444 chars, mirroring what a typical commit in this repository
        # looks like (a handful of modified Python files, no bulk output).
        median_size = 21_444
        # Three source-file chunks of roughly equal weight.
        chunk_lines = 140  # ~2,380 chars each × 3 files → ~7,100 chars of diff body
        diff = "".join(
            _source_diff(f"src/module_{i}.py", f"MEDIAN-{i}", lines=chunk_lines)
            for i in range(3)
        )
        # Pad with a few more files to reach the target size.
        while len(diff) < median_size:
            n = len(diff)
            diff += _source_diff(f"src/extra_{n}.py", f"PAD-{n}", lines=20)
        diff = diff[:median_size]  # trim to exactly the measured median

        assert len(diff) == median_size
        assert len(diff) < CHANGE_SECTION_CHAR_LIMIT, "precondition: diff must fit"
        section = render_change_block(ChangeContext(label="abc123..HEAD", diff=diff))
        assert "PART OF the change" not in section, (
            "a median-sized commit must not trigger truncation"
        )
        assert "CHANGED FILES" not in section

    def test_truncated_section_names_every_changed_file(self):
        section = render_change_block(
            ChangeContext(label="abc123..HEAD", diff=self._huge_diff())
        )
        for path in self.SOURCE_FILES + self.BULK_FILES:
            assert f"  - {path}\n" in section, f"{path} must survive truncation"

    def test_truncated_section_says_it_is_part_of_the_change(self):
        """A judge reading the section can tell 'this is the change' from
        'this is part of it': the notice is explicit, unlike the wholesale
        silence of an unbounded silent cut."""
        diff = self._huge_diff()
        section = render_change_block(ChangeContext(label="abc123..HEAD", diff=diff))
        assert "PART OF the change, not the whole of it" in section
        assert diff not in section
        assert "CHANGED FILES (5):" in section

    def test_source_content_survives_while_regenerated_bulk_gives_way(self):
        """The budget spends itself on what the judge most needs: the three
        source hunks are shown whole (sources take priority over bulk), the
        lockfile and bundle are named but cut — their deep content is gone
        and the cut is marked."""
        section = render_change_block(
            ChangeContext(label="abc123..HEAD", diff=self._huge_diff())
        )
        for i in range(len(self.SOURCE_FILES)):
            assert f"SOURCE-HUNK-{i}" in section
        for i in range(len(self.BULK_FILES)):
            assert f"BULK-{i}-old-version" not in section, "sentinel is past the cut"
            assert f"  - {self.BULK_FILES[i]}\n" in section, "name survives the cut"
        assert "more characters of this file's diff are not shown" in section

    def test_fitting_diff_is_shown_verbatim_without_any_notice(self):
        """Truncation must not disturb the common case: a wave's real source
        change is small, and a judge reading it sees exactly the diff the
        repository recorded, with no partial-ness claimed."""
        section = render_change_block(
            ChangeContext(label="abc123..HEAD", diff=CHANGE_DIFF)
        )
        assert CHANGE_DIFF in section
        assert "PART OF the change" not in section
        assert "CHANGED FILES" not in section

    def test_oversized_single_source_file_is_cut_with_a_marker(self):
        """Even when every file is hand-edited source, the section fits and
        the cut is marked, never silent.

        1500 lines is ~77k chars, well over the 64k section limit.  800 lines
        (~47k) was the original value; it was below the new limit and the test
        no longer exercises the truncation path at the old line count.
        """
        diff = _bulk_diff("src/big_migration.py", "MIGRATION", lines=1500)
        section = render_change_block(ChangeContext(label="abc123..HEAD", diff=diff))
        assert len(section) <= CHANGE_SECTION_CHAR_LIMIT
        assert "src/big_migration.py" in section
        assert "more characters of this file's diff are not shown" in section

    def test_bound_holds_for_many_files_markerless_and_binary_diffs(self):
        """The bound is a property of the section, not of a well-formed
        input: hundreds of files (name list itself bounded), a diff with no
        ``diff --git`` header at all, and a header-only binary chunk all
        stay within it, and every name that fits is still named."""
        many = "".join(
            _source_diff(f"src/module_{i:04d}.py", f"M{i}", lines=2)
            for i in range(300)
        )
        assert len(many) > CHANGE_SECTION_CHAR_LIMIT
        markerless = "x" * (50 * CHANGE_SECTION_CHAR_LIMIT)
        binary = _binary_diff("assets/logo.png") + (self._huge_diff())
        for diff in (many, markerless, binary):
            section = render_change_block(ChangeContext(label="a..b", diff=diff))
            assert len(section) <= CHANGE_SECTION_CHAR_LIMIT
        assert "src/module_0000.py" in render_change_block(
            ChangeContext(label="a..b", diff=many)
        )
        assert "  - assets/logo.png\n" in render_change_block(
            ChangeContext(label="a..b", diff=binary)
        )

    def test_name_list_is_truncated_but_says_so(self):
        """When even the names exceed the budget the section does not lie:
        it shows as many as fit and states plainly that more exist."""
        many = "".join(
            _source_diff(f"src/module_with_a_rather_long_name_{i:05d}.py", f"M{i}", lines=1)
            for i in range(1500)
        )
        section = render_change_block(ChangeContext(label="a..b", diff=many))
        assert len(section) <= CHANGE_SECTION_CHAR_LIMIT
        assert "more changed files (name list truncated)" in section

    def test_fit_diff_body_honours_a_tiny_budget(self):
        """``_fit_diff_body``'s own contract holds at any budget, including
        one too small to hold its markers — the clamp is the backstop for
        exactly that pathological case."""
        body, truncated = _fit_diff_body(self._huge_diff(), 40)
        assert truncated
        assert len(body) <= 40

    def test_giant_label_and_unreadable_diff_cannot_overflow(self):
        """Framing and the unreadable marker are input too: a pathological
        ref label or an oversized error string must not push the section
        past the bound, and the label is clipped so the notice and content
        are not what gets crowded out."""
        giant = "L" * 5000
        readable = render_change_block(
            ChangeContext(label=giant, diff=self._huge_diff())
        )
        unreadable = render_change_block(
            ChangeContext(label=f"unable to read diff {'L' * 30000}",
                          diff=f"(unable to read diff {'L' * 30000})",
                          readable=False)
        )
        for section in (readable, unreadable):
            assert len(section) <= CHANGE_SECTION_CHAR_LIMIT
        assert "PART OF the change" in readable

    def test_artifact_fallback_list_is_bounded_too(self):
        """No git view means the engine-recorded artifact list is the
        section; it gets the same ceiling and the same honesty marker.

        3000 entries is ~72k chars, over the 64k limit.  2000 entries (~48k)
        was the original count; it fits within the new limit and no longer
        exercises the truncation path.
        """
        artifacts = [f"src/module_{i:05d}.py" for i in range(3000)]
        section = render_change_block(None, artifacts)
        assert len(section) <= CHANGE_SECTION_CHAR_LIMIT
        assert "src/module_00000.py" in section
        assert "more files (list truncated)" in section
        assert render_change_block(None, []) == ""

    def test_the_bounded_section_reaches_the_judge_prompt(self):
        """The bound is not a property of the renderer alone: the injected
        prompt section a single-completion judge actually receives is the
        bounded one, notices and names included."""
        completion = _single_completion()
        _run("post_execute", [], completion, _git(diff=self._huge_diff()), MagicMock())
        prompt = _first_prompt(completion)
        assert "PART OF the change, not the whole of it" in prompt
        for path in self.SOURCE_FILES + self.BULK_FILES:
            assert path in prompt
        assert "BULK-0-old-version" not in prompt
