"""The coder's live stream reads like a validator's (Issue #306).

A validator's turn reaches the sink composed — "[0:28] Turn 5:
read_file(...)". A subprocess coder's reaches it raw. These tests pin the
shape the live view now gives it, and the promises that bound the change:

- a recognised coder line is re-emitted with elapsed time, a turn count and
  what the turn did, in exactly the validator's line shape;
- an unrecognised line reaches the operator verbatim — nothing is dropped,
  and a line nobody's pattern claims is never guessed at;
- the shaping belongs to the interactive path only: a non-tty stream and
  ``NO_COLOR`` keep producing today's plain, verbatim lines, so the job's
  stdout.log is byte-identical;
- the shape is derived from what the line already states and lives entirely
  in the renderer — no engine fact, verdict or halt reads it (ADR 034).
"""

import io
import re

from snodo.engine.progress import (
    ProgressRenderer,
    classify_progress_line,
    summarize_coder_line,
)

_TURN_SHAPE_RE = re.compile(r"^    \[\d+:\d{2}\] Turn \d+: \w+\(.+\)$")

# Coder output shapes seen across the host CLIs: a bare verb line, a bullet
# with the verb lowercase, a checkbox status, a shell prompt echo.
_RECOGNISED = {
    "Read src/lib/card.ts": "read_file(src/lib/card.ts)",
    "  reading config/settings.toml": "read_file(config/settings.toml)",
    "→  edit site/src/App.tsx": "edit_file(site/src/App.tsx)",
    "✔ Patched lib/utils.py": "edit_file(lib/utils.py)",
    "Wrote out/report.json": "write_file(out/report.json)",
    "- Creating module pkg/core.py": "write_file(pkg/core.py)",
    "Removed old/legacy.sh": "delete_file(old/legacy.sh)",
    "$ make build": "run(make build)",
    "Running pytest -q tests/test_x.py": "run(pytest -q tests/test_x.py)",
    "Listing docs/": "list_files(docs/)",
    "grep TODO in src": "search(TODO in src)",
    "Search for card component": "search(card component)",
}

# Lines no pattern may claim: prose, narration, build noise, source code,
# and near-misses whose target is not a path. Passing these through beats
# guessing at them.
_UNRECOGNISED = [
    "I will read the config and update it.",
    "Sure, let me read the manifest and look at the tests.",
    "Traceback (most recent call last):",
    "    42 sites",
    "tests/test_x.py::test_one PASSED",
    "Compiling wasm v2.1",
    "",
    "Read the documentation carefully",
    "README",
]


class TestSummarizeCoderLine:
    def test_recognised_lines_say_what_the_turn_did(self):
        for line, expected in _RECOGNISED.items():
            assert summarize_coder_line(line) == expected, line

    def test_unrecognised_lines_are_not_claimed(self):
        for line in _UNRECOGNISED:
            assert summarize_coder_line(line) is None, line

    def test_a_long_target_is_capped_so_one_path_cannot_widen_the_row(self):
        summary = summarize_coder_line("Read " + "x" * 20 + "/y" * 40 + ".ts")
        assert summary is not None
        assert len(summary) <= len("read_file()") + 60 + 3


class TestLiveViewShape:
    def _renderer(self):
        return ProgressRenderer(stream=io.StringIO(), color=True, window=20)

    def test_recognised_coder_lines_carry_turn_count_and_elapsed(self):
        r = self._renderer()
        r("  Coder dispatched")
        r("Read src/lib/card.ts")
        r("$ make build")
        turn_lines = [row for row in r.visible_lines() if _TURN_SHAPE_RE.match(row)]
        assert len(turn_lines) == 2
        # The validator's shape exactly: the lines classify as turns.
        for row in turn_lines:
            assert classify_progress_line(row) == "turn", row
        assert turn_lines[0].endswith("Turn 1: read_file(src/lib/card.ts)")
        assert turn_lines[1].endswith("Turn 2: run(make build)")

    def test_turn_number_counts_turns_not_elapsed_wall(self):
        r = self._renderer()
        r("  Coder dispatched")
        for i in range(5):
            r(f"Read file_{i}.ts")
        turns = [row for row in r.visible_lines() if _TURN_SHAPE_RE.match(row)]
        assert [re.search(r"Turn (\d+)", t).group(1) for t in turns] == ["1", "2", "3", "4", "5"]

    def test_unrecognised_lines_still_reach_the_operator_verbatim(self):
        r = self._renderer()
        r("  Coder dispatched")
        prose = "I will read the config and update it."
        r(prose)
        assert prose in r.visible_lines()

    def test_only_recognised_lines_claim_a_turn_number(self):
        r = self._renderer()
        r("  Coder dispatched")
        r("noise from the build system")
        r("Read src/a.ts")
        shaped = [row for row in r.visible_lines() if _TURN_SHAPE_RE.match(row)]
        assert len(shaped) == 1
        assert shaped[0].endswith("Turn 1: read_file(src/a.ts)")

    def test_the_shape_ends_with_the_coder_phase(self):
        """After the coder returns, later lines are the engine's again."""
        r = self._renderer()
        r("  Coder dispatched")
        r("Read src/a.ts")
        r("  Coder returned (2 artifact(s))")
        r("Read src/b.ts")  # engine prose after the phase: left alone
        visible = r.visible_lines()
        assert "    [0:00] Turn 1: read_file(src/a.ts)" in visible or any(
            v.endswith("Turn 1: read_file(src/a.ts)") for v in visible
        )
        assert "Read src/b.ts" in visible
        assert not any(v.endswith("Turn 2: read_file(src/b.ts)") for v in visible)

    def test_a_recognisable_turn_line_is_not_recounted(self):
        """A litellm coder composes its own turns; shaping never stacks twice."""
        r = self._renderer()
        r("  Coder dispatched")
        r("    [0:03] Turn 2: read_file(a.py)")
        assert "    [0:03] Turn 2: read_file(a.py)" in r.visible_lines()

    def test_a_new_dispatch_starts_a_new_turn_count(self):
        r = self._renderer()
        r("  Coder dispatched")
        r("Read src/a.ts")
        r("  Coder returned (1 artifact(s))")
        r("  Coder dispatched")
        r("Read src/b.ts")
        turns = [row for row in r.visible_lines() if _TURN_SHAPE_RE.match(row)]
        assert [re.search(r"Turn (\d+)", t).group(1) for t in turns] == ["1", "1"]


class TestRecordStaysRaw:
    def test_non_tty_stream_is_byte_identical_to_the_coder_raw_lines(self):
        """The stdout.log path: every coder line lands exactly as written."""
        lines = ["  Coder dispatched", *list(_RECOGNISED), *_UNRECOGNISED]
        out = io.StringIO()
        r = ProgressRenderer(stream=out, color=False)
        for line in lines:
            r(line)
        assert out.getvalue() == "".join(f"{line}\n" for line in lines)
        assert "\x1b" not in out.getvalue()

    def test_no_color_keeps_producing_plain_lines(self):
        lines = ["  Coder dispatched", "Read src/lib/card.ts"]
        out = io.StringIO()
        out.isatty = lambda: True  # type: ignore[attr-defined]
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            r = ProgressRenderer(stream=out)
            for line in lines:
                r(line)
        assert out.getvalue() == "".join(f"{line}\n" for line in lines)
