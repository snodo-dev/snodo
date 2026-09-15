"""A recovery spec that cites evidence the tree no longer holds must say so.

An earlier attempt can fix the very construct a spec opens with; the next
recovery coder is then sent to find a defect that is gone (Fixes #286).  The
engine checks the spec's checkable presence claims against the worktree and
halts with the existing ``escalated`` outcome instead of dispatching.  It never
rewrites the spec: what the task now means is not the engine's call (#35).

The check is deliberately narrow.  These tests pin both directions: a removed
construct is reported, and a spec that still holds (or that merely states a
goal, or whose construct only moved) dispatches unchanged.
"""

from pathlib import Path
from unittest.mock import MagicMock

from snodo.compiler.models import DisagreementPolicy, Mode, Protocol, Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.engine.loop import GraphBuilder
from snodo.engine.premise import extract_citations, find_stale_citations
from snodo.engine.state import LoopState
from snodo.tools.workspace import WorkspaceMCP

STALE_SPEC = (
    "The booking field in `src/booking.js` is `<input type=\"url\">` with the "
    "placeholder cal.com/you; the defect follows from the wrong input type."
)


def _protocol():
    validator = Validator(validator_id="quality", validator_type="exec", command="true")
    return Protocol(
        protocol_id="p",
        name="p",
        initial_mode="build",
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        validators=[validator],
        modes=[
            Mode(
                mode_id="build",
                name="build",
                validators=["quality"],
                max_recovery_depth=3,
            )
        ],
    )


def _blocker():
    return [ValidatorResult(validator_id="quality", severity="blocker", justification="broken")]


def _write_tree(root: Path, booking_body: str) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "booking.js").write_text(booking_body, encoding="utf-8")


# ---------------------------------------------------------------------------
# The check itself: a presence claim is only flagged when the construct is gone
# ---------------------------------------------------------------------------

class TestStaleCitationDetection:
    def test_removed_construct_is_stale(self, tmp_path):
        _write_tree(tmp_path, "const field = '<input type=\"text\">';")
        stale = find_stale_citations(STALE_SPEC, tmp_path)
        assert [c.construct for c in stale] == ['<input type="url">']
        assert stale[0].path == "src/booking.js"

    def test_holding_construct_is_not_stale(self, tmp_path):
        _write_tree(tmp_path, "const field = `<input type=\"url\">`;")
        assert find_stale_citations(STALE_SPEC, tmp_path) == []

    def test_construct_that_moved_is_not_stale(self, tmp_path):
        # Gone from the cited file but still somewhere in the tree: the defect
        # is not gone, so the engine must not call the premise stale.
        _write_tree(tmp_path, "const field = '<input type=\"text\">';")
        (tmp_path / "src" / "website.js").write_text(
            "const other = `<input type=\"url\">`;", encoding="utf-8"
        )
        assert find_stale_citations(STALE_SPEC, tmp_path) == []

    def test_whitespace_reformat_is_not_stale(self, tmp_path):
        _write_tree(tmp_path, "const field = `<input   type=\"url\">`;")
        assert find_stale_citations(STALE_SPEC, tmp_path) == []

    def test_goal_does_not_assert_presence(self, tmp_path):
        _write_tree(tmp_path, "const field = '<input type=\"text\">';")
        goal = "Add `<input type=\"url\">` to the booking field in `src/booking.js`."
        assert find_stale_citations(goal, tmp_path) == []
        assert extract_citations(goal) == []

    def test_obligation_with_modal_does_not_assert_presence(self, tmp_path):
        _write_tree(tmp_path, "const field = '<input type=\"text\">';")
        obligation = "The booking field should have `<input type=\"url\">`."
        assert find_stale_citations(obligation, tmp_path) == []

    def test_bare_identifier_is_not_a_structural_claim(self, tmp_path):
        # "the function is `oldName`" is how a rename task names what it is
        # changing; a bare identifier must never be read as stale evidence.
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "thing.js").write_text("const renamed = 1;", encoding="utf-8")
        rename = "The function in `src/thing.js` is `oldName`; rename it to `newName`."
        assert find_stale_citations(rename, tmp_path) == []

    def test_unknown_root_reports_nothing(self, tmp_path):
        assert find_stale_citations(STALE_SPEC, tmp_path / "does-not-exist") == []

    def test_incomplete_scan_never_reports_stale(self, tmp_path, monkeypatch):
        # If the scan cannot cover the tree, absence is unproven and the spec
        # is left alone: a false "stale" is worse than no check.
        from snodo.engine import premise

        _write_tree(tmp_path, "const field = '<input type=\"text\">';")
        (tmp_path / "src" / "other.js").write_text("const y = 1;", encoding="utf-8")
        monkeypatch.setattr(premise, "_MAX_FILES", 1)
        assert find_stale_citations(STALE_SPEC, tmp_path) == []


# ---------------------------------------------------------------------------
# Through the recovery spawn: stale halts, holding dispatches unchanged
# ---------------------------------------------------------------------------

def _spawn(tmp_path, spec: str):
    builder = GraphBuilder(
        _protocol(),
        workspace_mcp=WorkspaceMCP(str(tmp_path)),
        validator_fn=lambda *a, **k: _blocker(),
    )
    builder._progress = lambda *a, **k: None
    loop_state = LoopState(
        task=Task(id="t1", spec=spec, root_task_ref="t1", root_spec=spec, depth=0),
        current_mode="build",
    )
    builder._spawn_recovery_subtask(loop_state, _blocker(), MagicMock())
    return loop_state


class TestRecoveryPremiseGate:
    def test_stale_spec_halts_instead_of_dispatching(self, tmp_path):
        _write_tree(tmp_path, "const field = '<input type=\"text\">';")
        loop_state = _spawn(tmp_path, STALE_SPEC)

        assert loop_state.is_blocked is True
        assert loop_state.halt_type == "escalated"
        assert loop_state.spawned_subtasks == []
        assert loop_state.needs_recovery is False
        assert any(
            "premise is stale" in v and "input" in v
            for v in loop_state.constraint_violations
        )

    def test_holding_spec_dispatches_unchanged(self, tmp_path):
        _write_tree(tmp_path, "const field = `<input type=\"url\">`;")
        loop_state = _spawn(tmp_path, STALE_SPEC)

        assert loop_state.is_blocked is False
        assert len(loop_state.spawned_subtasks) == 1
        fix = loop_state.spawned_subtasks[0]
        # The spec is carried verbatim: the engine reports a stale premise, it
        # does not rewrite the spec.
        assert STALE_SPEC in fix.spec
        assert fix.spec.startswith("The task is the INTENT below")
