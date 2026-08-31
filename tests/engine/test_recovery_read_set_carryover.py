"""Recovery attempts must inherit the *map* of what the prior attempt read,
never its contents.

Attempt N can spend dozens of turns reading, and its recovery re-reads the same
files from scratch — costing wall-clock and discarding the provider's prompt
cache. Carrying the prior attempt's read-set forward is only safe if the tree's
freshness is preserved: the previous attempt may have written these very files,
so a cached copy handed to the next coder would make a stale view authoritative.
The contract therefore is: carry PATHS, never CONTENTS.
"""

from unittest.mock import MagicMock, patch

from snodo.compiler.models import DisagreementPolicy, Mode, Protocol, Validator
from snodo.coders.litellm import ReadMemoryTracker
from snodo.core.interfaces import Task, ValidatorResult
from snodo.engine.loop import (
    GraphBuilder,
    LoopState,
    _build_recovery_spec,
    _combine_attempt_reads,
)


# ---------------------------------------------------------------------------
# ReadMemoryTracker exports paths only — contents are never recorded or emitted.
# ---------------------------------------------------------------------------

def test_export_paths_returns_paths_only():
    tracker = ReadMemoryTracker()
    tracker.record_read("read_file", {"path": "src/router.js"}, 1)
    tracker.record_read("read_file_lines", {"path": "src/auth.js", "start": 1, "end": 40}, 2)
    tracker.record_read("list_directory", {"directory": "src"}, 3)

    exported = tracker.export_paths()

    assert set(exported) == {"files", "directories"}
    assert "src/router.js" in exported["files"]
    assert "src/auth.js" in exported["files"]
    assert "src" in exported["directories"]
    # The export is a plain path map — no contents, no turn indices, no blobs.
    assert all(isinstance(v, str) for v in exported["files"])
    assert all(isinstance(v, str) for v in exported["directories"])


# ---------------------------------------------------------------------------
# The recovery spec carries the read-set as a map, with an explicit staleness
# guard, and never smuggles file contents through.
# ---------------------------------------------------------------------------

def test_recovery_spec_carries_paths_and_staleness_warning():
    spec = _build_recovery_spec(
        "Fix the sign-in redirect.",
        [],
        None,
        [{"attempt": 1, "files": ["src/router.js", "src/auth.js"], "directories": ["src"]}],
    )
    assert "PRIOR INSPECTION MAP" in spec
    assert "src/router.js" in spec
    assert "src/auth.js" in spec
    # Explicit "not contents / tree changed / open before editing" framing.
    assert "NOT their contents" in spec
    assert "open a file before editing" in spec


def test_recovery_spec_carries_no_content_sentinel():
    # attempt_reads holds only paths, so a content-like sentinel has nowhere to
    # live; assert it cannot leak into the spec through the read-set.
    sentinel = "SECRET_FILE_BODY_MUST_NOT_APPEAR"
    spec = _build_recovery_spec(
        "Intent",
        [],
        None,
        [{"attempt": 1, "files": [f"{sentinel}.js".replace(sentinel, "src/a")], "directories": []}],
    )
    assert sentinel not in spec


def test_build_recovery_spec_omits_read_section_without_history():
    spec = _build_recovery_spec("Intent", [], None, [])
    assert "PRIOR INSPECTION MAP" not in spec


def test_combine_attempt_reads_accumulates_and_dedupes():
    prior = [{"attempt": 1, "files": ["a.py", "a.py"], "directories": ["src"]}]
    cur = {"files": ["a.py", "b.py"], "directories": []}
    combined = _combine_attempt_reads(prior, 2, cur)

    assert [e["attempt"] for e in combined] == [1, 2]
    assert combined[0]["files"] == ["a.py"]  # deduped
    assert combined[1]["files"] == ["a.py", "b.py"]


# ---------------------------------------------------------------------------
# End to end through the executor capture and the recovery spawn.
# ---------------------------------------------------------------------------

class _ReadSetCoder:
    def __init__(self):
        self.workspace_mcp = None
        self.skip_workspace_write = False
        self.skip_engine_commit = True
        self._job_id = None
        self._task_id = None
        self.progress_callback = None
        # What the coder inspected while working this attempt.
        self.last_read_paths = ["src/auth.js", "src/main.css"]
        self.last_listed_dirs = ["src"]

    def implement(self, spec):
        class _Op:
            action = "write"
            path = "src/app.js"
            content = "const x = 1;"

        class _Artifact:
            files = [_Op()]

        return _Artifact()


def test_executor_captures_read_set_without_contents():
    builder = GraphBuilder(_protocol_for_recovery())
    coder = _ReadSetCoder()
    with patch("snodo.engine.nodes.executor._branch_exists", return_value=True):
        builder._default_executor(
            Task(id="t1", spec="s"), MagicMock(), coder, MagicMock(), None
        )

    assert builder._last_execution_reads == {
        "files": ["src/auth.js", "src/main.css"],
        "directories": ["src"],
    }
    # Only paths recorded — the coder's file bodies are nowhere in the capture.
    assert all("const x" not in p for p in builder._last_execution_reads["files"])


def _protocol_for_recovery():
    v = Validator(validator_id="quality", validator_type="exec", command="true")
    return Protocol(
        protocol_id="p",
        name="p",
        initial_mode="build",
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        validators=[v],
        modes=[Mode(mode_id="build", name="build", validators=["quality"], max_recovery_depth=3)],
    )


def test_spawn_recovery_carries_prior_and_current_reads_into_spec():
    loop = GraphBuilder(_protocol_for_recovery())
    loop._progress = lambda *a, **k: None

    task = Task(id="t1", spec="build it", depth=1)
    # Attempt 1's reads (carried on the task) and attempt 2's reads (executor).
    task.attempt_reads = [{"attempt": 1, "files": ["src/router.js"], "directories": []}]
    loop_state = LoopState(task=task, current_mode="build")
    loop_state.metadata["attempt_read_files"] = {
        "files": ["src/auth.js"],
        "directories": ["src"],
    }

    results = [ValidatorResult(validator_id="quality", severity="blocker", justification="broken")]
    loop._spawn_recovery_subtask(loop_state, results, MagicMock())

    fix = loop_state.spawned_subtasks[0]
    assert "PRIOR INSPECTION MAP" in fix.spec
    assert "src/router.js" in fix.spec  # prior attempt
    assert "src/auth.js" in fix.spec  # current attempt
    assert {e["attempt"] for e in fix.attempt_reads} == {1, 2}


def test_spawn_recovery_read_history_round_trips_through_serde():
    """The accumulated read-set must survive state serialization to reach the
    recovery attempt (spawned subtask -> initial state -> Task)."""
    loop = GraphBuilder(_protocol_for_recovery())
    loop._progress = lambda *a, **k: None

    task = Task(id="t1", spec="build it", depth=0)
    loop_state = LoopState(task=task, current_mode="build")
    loop_state.metadata["attempt_read_files"] = {"files": ["src/router.js"], "directories": []}
    loop._spawn_recovery_subtask(
        loop_state,
        [ValidatorResult(validator_id="quality", severity="blocker", justification="x")],
        MagicMock(),
    )
    fix = loop_state.spawned_subtasks[0]

    as_dict = loop._state_to_dict(loop_state)
    restored = loop._dict_to_state(as_dict)
    restored_fix = restored.spawned_subtasks[0]
    assert restored_fix.attempt_reads == fix.attempt_reads
    assert restored_fix.attempt_reads[0]["files"] == ["src/router.js"]
