"""Presentation of the run stream (Issue #294, #300).

The engine already emits every kind of line this renders; these tests pin the
properties the change is allowed to have, and the ones it must not:

- with colour unavailable (a non-tty, a redirected stream, ``NO_COLOR``) the
  output is byte-identical to what the sink produced before #294 — the same
  lines, in the same order, with no escape sequences;
- with colour on, a halt line is distinguishable from a turn line (they carry
  different styling);
- with colour on, the live view is a scrolling window of recent lines rather
  than a single overwritten row (#300): a verdict or phase boundary stays on
  screen as later turns arrive, and only an *identical* consecutive turn line
  collapses into the row it repeats.

Classification is by the shape of the line the engine already emits, so a
change to what the sink reports does not silently change the rendering.
"""

import io
import os
import re
from unittest.mock import patch

from snodo.engine.progress import (
    ACTIVITY,
    CODER,
    GATE_FAIL,
    GATE_OK,
    GATE_WARN,
    HALT,
    HEARTBEAT,
    PHASE,
    PLAIN,
    TURN,
    ProgressRenderer,
    classify_progress_line,
    color_enabled,
    default_window_size,
    render_progress_line,
)

# Lines the engine actually emits, each mapped to the kind it must classify as.
_ENGINE_LINES = {
    "    [0:14] Turn 9: list_files(site/src/routes), list_files(site/src/lib)": TURN,
    "  Validating (pre-execute): security, quality": PHASE,
    "  Post-validating: quality": PHASE,
    "  Spec authored (attempt 1), triggered by security": PHASE,
    "  Coder dispatched": CODER,
    "  Coder returned (3 artifact(s))": CODER,
    "  Prepared environment: npm install": CODER,
    "  Recovery (attempt 2/3): spawned t_fix_2 (quality (warn))": CODER,
    "    quality: started": ACTIVITY,
    "    quality: finished": ACTIVITY,
    "    ✓ quality: pass": GATE_OK,
    "    ✓ quality: pass (skipped) — tests": GATE_OK,
    "    ⚠️ quality: warn — something": GATE_WARN,
    "    ❌ quality: blocker — bad": GATE_FAIL,
    "    💥 quality: error (blocker)": GATE_FAIL,
    "  Recovery stalled (attempt 2/3): identical verdict; halting loop": HALT,
    "  Recovery depth exhausted (depth 2/2): limit reached; halting loop": HALT,
    "  Recovery premise stale (attempt 2/3): x; halting instead of dispatching": HALT,
    "  ~ watching · plan 1:20 · no status change": HEARTBEAT,
    "Some narration no rule claims": PLAIN,
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def test_engine_lines_classify_by_their_emitted_shape():
    for line, kind in _ENGINE_LINES.items():
        assert classify_progress_line(line) == kind, line


def test_turn_past_an_hour_still_reads_as_a_turn():
    """A run past an hour labels its turns h:mm:ss (#316); the compaction rule
    widens with the renderer rather than silently dropping such turns to plain."""
    assert classify_progress_line("    [1:04:11] Turn 9: read_file(a.py)") == TURN
    assert classify_progress_line("    [22:35] Turn 9: read_file(a.py)") == TURN


def test_non_tty_stream_is_rendered_verbatim():
    """Colour off: the exact bytes the sink produced, no escape sequences."""
    lines = list(_ENGINE_LINES)
    out = io.StringIO()
    renderer = ProgressRenderer(stream=out, color=False)
    for line in lines:
        renderer(line)
    expected = "".join(f"{line}\n" for line in lines)
    assert out.getvalue() == expected
    assert "\x1b" not in out.getvalue()


def test_no_color_disables_even_on_a_tty():
    stream = io.StringIO()
    stream.isatty = lambda: True  # type: ignore[attr-defined]
    with patch.dict("os.environ", {"NO_COLOR": "1"}):
        assert color_enabled(stream) is False
    with patch.dict("os.environ", {"NO_COLOR": ""}):
        assert color_enabled(stream) is True


def test_color_enabled_requires_a_tty():
    piped = io.StringIO()
    piped.isatty = lambda: False  # type: ignore[attr-defined]
    with patch.dict("os.environ", {"NO_COLOR": ""}):
        assert color_enabled(piped) is False


def test_render_progress_line_without_color_is_identity():
    for line in _ENGINE_LINES:
        assert render_progress_line(line, color=False) == line


def test_halt_line_is_distinguishable_from_turn_line_with_color_on():
    turn = "    [0:14] Turn 9: list_files(site/src/routes)"
    halt = "  Recovery stalled (attempt 2/3): identical verdict; halting loop"
    turn_rendered = render_progress_line(turn, color=True)
    halt_rendered = render_progress_line(halt, color=True)
    assert turn_rendered != halt_rendered
    assert "\x1b" in turn_rendered and "\x1b" in halt_rendered
    # Stripping the styling recovers the original text exactly.
    assert _ANSI_RE.sub("", turn_rendered) == turn
    assert _ANSI_RE.sub("", halt_rendered) == halt
    # The styles themselves differ, so the two never read alike.
    assert turn_rendered != halt_rendered


def test_coloured_renderer_styles_and_keeps_every_line():
    """With colour on, every line still reaches the stream in order."""
    lines = list(_ENGINE_LINES)
    out = io.StringIO()
    renderer = ProgressRenderer(stream=out, color=True, window=len(lines))
    for line in lines:
        renderer(line)
    rendered = out.getvalue()
    assert "\x1b" in rendered
    # Stripping ANSI recovers the plain sequence.
    stripped = _ANSI_RE.sub("", rendered)
    for line in lines:
        assert line in stripped
    assert renderer.visible_lines() == lines


def test_distinct_turn_lines_each_get_their_own_row():
    """Different judges' turn lines never collapse into each other (#300):

    #294 compacted every consecutive TURN-kind line regardless of content,
    which is exactly what hid concurrent validators behind one changing row.
    Only an *identical* repeat collapses; distinct turns are distinct rows.
    """
    out = io.StringIO()
    renderer = ProgressRenderer(stream=out, color=True, window=10)
    renderer("    [0:01] Turn 1: read_file(a.py)")
    renderer("    [0:02] Turn 2: read_file(b.py)")
    renderer("    [0:03] Turn 3: read_file(c.py)")
    assert renderer.visible_lines() == [
        "    [0:01] Turn 1: read_file(a.py)",
        "    [0:02] Turn 2: read_file(b.py)",
        "    [0:03] Turn 3: read_file(c.py)",
    ]


def test_identical_consecutive_turn_lines_collapse_into_one_row():
    """A judge repeating the exact same read collapses, counted, not stacked."""
    out = io.StringIO()
    renderer = ProgressRenderer(stream=out, color=True, window=10)
    renderer("    [0:01] Turn 1: read_file(a.py)")
    renderer("    [0:02] Turn 2: read_file(a.py)")
    renderer("    [0:02] Turn 2: read_file(a.py)")
    renderer("    [0:02] Turn 2: read_file(a.py)")
    assert renderer.visible_lines() == [
        "    [0:01] Turn 1: read_file(a.py)",
        "    [0:02] Turn 2: read_file(a.py)  (×3)",
    ]


# ── Heartbeat runs share one repainted row (Issue #321) ────────────────

_HB_120 = "  ~ watching · plan 1:20 · no status change"
_HB_125 = "  ~ watching · plan 1:25 · no status change"
_HB_130 = "  ~ watching · plan 1:30 · no status change"


def test_consecutive_heartbeats_share_one_repainted_row():
    """A run of heartbeats occupies one row that is repainted with each newer
    text (#321): differing only by the elapsed time they show, they are not
    identical, yet an hour of waiting must not cost a row apiece."""
    out = io.StringIO()
    renderer = ProgressRenderer(stream=out, color=True, window=10)
    renderer(_HB_120)
    renderer(_HB_125)
    renderer(_HB_130)
    # One row, current: the newest numbers are what the operator sees.
    assert renderer.visible_lines() == [_HB_130]
    rendered = out.getvalue()
    # The later heartbeats were drawn by moving the cursor up onto the row
    # they share — a repaint, not a new line — and nothing is counted with a
    # (×N) the way a repeated turn would be.
    assert "\x1b[1A" in rendered
    assert "×" not in rendered


def test_real_event_between_heartbeats_ends_the_run():
    """A turn arriving below the heartbeat row ends its run: the event takes
    its own row, and the next heartbeat starts a fresh row beneath it (#321),
    so the history of real events stays intact and only the waiting collapses.
    """
    turn = "    [0:28] Turn 5: read_file(src/app.tsx)"
    out = io.StringIO()
    renderer = ProgressRenderer(stream=out, color=True, window=10)
    renderer(_HB_120)
    renderer(_HB_125)
    renderer(turn)
    renderer(_HB_130)
    renderer(_HB_120)  # a new run of waiting, on a row of its own
    assert renderer.visible_lines() == [_HB_125, turn, _HB_120]


def test_collapse_stays_narrow_to_heartbeat_lines():
    """The repaint rule belongs to heartbeats alone: look-alike lines — a
    repeated verdict, a near-miss marker — never share a row with anything."""
    # Only the watch's own shape claims the kind.
    assert classify_progress_line("Watching: nothing") == PLAIN
    assert classify_progress_line("~watching") == PLAIN
    assert classify_progress_line("  ~ waiting for status") == PLAIN

    out = io.StringIO()
    renderer = ProgressRenderer(stream=out, color=True, window=10)
    renderer("    ✓ quality: pass")
    renderer("    ✓ quality: pass")
    assert renderer.visible_lines() == [
        "    ✓ quality: pass",
        "    ✓ quality: pass",
    ]
    # A turn followed by a heartbeat: two rows; the heartbeat claims a fresh
    # one rather than painting over the turn.
    out2 = io.StringIO()
    renderer2 = ProgressRenderer(stream=out2, color=True, window=10)
    renderer2("    [0:01] Turn 1: read_file(a.py)")
    renderer2(_HB_120)
    renderer2(_HB_125)
    assert renderer2.visible_lines() == ["    [0:01] Turn 1: read_file(a.py)", _HB_125]


def test_verdict_line_stays_visible_after_later_turns_arrive():
    """A verdict reached mid-run survives later turns instead of being
    overwritten by the very next read (the core #300 complaint)."""
    out = io.StringIO()
    renderer = ProgressRenderer(stream=out, color=True, window=10)
    renderer("    architecture: started")
    renderer("    ⚠️ architecture: warn — layering drifted")
    renderer("    architecture: finished")
    renderer("    [0:04] Turn 5: read_file(x.py)")
    renderer("    [0:05] Turn 6: read_file(y.py)")
    visible = renderer.visible_lines()
    assert "    ⚠️ architecture: warn — layering drifted" in visible
    assert "    architecture: finished" in visible


def test_window_scrolls_oldest_line_out_once_full():
    out = io.StringIO()
    renderer = ProgressRenderer(stream=out, color=True, window=3)
    renderer("  Coder dispatched")
    renderer("    [0:01] Turn 1: read_file(a.py)")
    renderer("    [0:02] Turn 2: read_file(b.py)")
    renderer("    [0:03] Turn 3: read_file(c.py)")
    # Four distinct events, a window of three: the oldest has scrolled off.
    assert renderer.visible_lines() == [
        "    [0:01] Turn 1: read_file(a.py)",
        "    [0:02] Turn 2: read_file(b.py)",
        "    [0:03] Turn 3: read_file(c.py)",
    ]


def test_reset_clears_the_window():
    out = io.StringIO()
    renderer = ProgressRenderer(stream=out, color=True, window=10)
    renderer("    [0:01] Turn 1: read_file(a.py)")
    renderer.reset()
    assert renderer.visible_lines() == []
    renderer("    [0:02] Turn 2: read_file(b.py)")
    assert renderer.visible_lines() == ["    [0:02] Turn 2: read_file(b.py)"]


def test_default_window_size_prefers_an_explicit_override(monkeypatch):
    monkeypatch.setenv("SNODO_WATCH_WINDOW", "7")
    assert default_window_size(io.StringIO()) == 7


def test_default_window_size_ignores_a_non_numeric_override(monkeypatch):
    monkeypatch.setenv("SNODO_WATCH_WINDOW", "not-a-number")
    with patch("shutil.get_terminal_size", return_value=os.terminal_size((80, 24))):
        assert default_window_size(io.StringIO()) == 20


def test_default_window_size_scales_with_terminal_height(monkeypatch):
    """Not a hard-coded number: a taller terminal earns a bigger window."""
    monkeypatch.delenv("SNODO_WATCH_WINDOW", raising=False)
    with patch("shutil.get_terminal_size", return_value=os.terminal_size((80, 50))):
        assert default_window_size(io.StringIO()) == 46


def test_default_window_size_has_a_floor_on_a_short_terminal(monkeypatch):
    """A short terminal still gets more than one line, reachable via override."""
    monkeypatch.delenv("SNODO_WATCH_WINDOW", raising=False)
    with patch("shutil.get_terminal_size", return_value=os.terminal_size((80, 5))):
        assert default_window_size(io.StringIO()) == 5


def _builder_with_verbose(verbose=True):
    """A GraphBuilder with only the attributes _progress reads."""
    from snodo.engine.loop import GraphBuilder

    builder = object.__new__(GraphBuilder)
    builder._verbose = verbose
    builder._progress_renderer = None
    return builder


def test_graphbuilder_progress_is_byte_identical_on_a_pipe(monkeypatch):
    """The loop's progress sink on a non-tty writes exactly today's lines."""
    import sys

    lines = list(_ENGINE_LINES)
    out = io.StringIO()
    out.isatty = lambda: False  # type: ignore[attr-defined]
    monkeypatch.delenv("NO_COLOR", raising=False)
    builder = _builder_with_verbose()
    with patch.object(sys, "stdout", out):
        for line in lines:
            builder._progress(line)
    assert out.getvalue() == "".join(f"{line}\n" for line in lines)


def test_graphbuilder_progress_is_byte_identical_under_no_color(monkeypatch):
    """NO_COLOR produces today's plain lines even on a tty."""
    import sys

    lines = list(_ENGINE_LINES)
    out = io.StringIO()
    out.isatty = lambda: True  # type: ignore[attr-defined]
    monkeypatch.setenv("NO_COLOR", "1")
    builder = _builder_with_verbose()
    with patch.object(sys, "stdout", out):
        for line in lines:
            builder._progress(line)
    assert out.getvalue() == "".join(f"{line}\n" for line in lines)


def test_graphbuilder_progress_verbose_gate_is_unchanged():
    """The verbose gate still suppresses fine detail unless opted in."""
    import sys

    out = io.StringIO()
    out.isatty = lambda: False  # type: ignore[attr-defined]
    builder = _builder_with_verbose(verbose=False)
    with patch.object(sys, "stdout", out):
        builder._progress("  normal line")
        builder._progress("  fine detail", verbose=True)
    assert out.getvalue() == "  normal line\n"

