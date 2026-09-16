"""Presentation of the run stream (Issue #294).

The engine already emits every kind of line this renders; these tests pin the
two properties the change is allowed to have, and the two it must not:

- with colour unavailable (a non-tty, a redirected stream, ``NO_COLOR``) the
  output is byte-identical to what the sink produced before #294 — the same
  lines, in the same order, with no escape sequences;
- with colour on, a halt line is distinguishable from a turn line (they carry
  different styling), and repeated turn lines compact in place rather than
  accumulating.

Classification is by the shape of the line the engine already emits, so a
change to what the sink reports does not silently change the rendering.
"""

import io
import re
from unittest.mock import patch

from snodo.engine.progress import (
    ACTIVITY,
    CODER,
    GATE_FAIL,
    GATE_OK,
    GATE_WARN,
    HALT,
    PHASE,
    PLAIN,
    TURN,
    ProgressRenderer,
    classify_progress_line,
    color_enabled,
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
    "Some narration no rule claims": PLAIN,
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def test_engine_lines_classify_by_their_emitted_shape():
    for line, kind in _ENGINE_LINES.items():
        assert classify_progress_line(line) == kind, line


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
    renderer = ProgressRenderer(stream=out, color=True)
    for line in lines:
        renderer(line)
    rendered = out.getvalue()
    assert "\x1b" in rendered
    # Stripping ANSI and the in-place overwrites recovers the plain sequence.
    stripped = _ANSI_RE.sub("", rendered)
    for line in lines:
        assert line in stripped


def test_consecutive_turns_compact_in_place():
    """Repeated turn lines overwrite rather than accumulating."""
    out = io.StringIO()
    renderer = ProgressRenderer(stream=out, color=True)
    renderer("    [0:01] Turn 1: read_file(a.py)")
    renderer("    [0:02] Turn 2: read_file(b.py)")
    renderer("    [0:03] Turn 3: read_file(c.py)")
    rendered = out.getvalue()
    # Three turn writes, but only one is a fresh line: the later two move up.
    assert rendered.count("\x1b[1A") == 2
    assert rendered.count("\n") == 3


def test_turn_compaction_resets_after_a_non_turn_line():
    """A phase or halt between turns starts a fresh line, not an overwrite."""
    out = io.StringIO()
    renderer = ProgressRenderer(stream=out, color=True)
    renderer("    [0:01] Turn 1: read_file(a.py)")
    renderer("  Validating (pre-execute): security")
    renderer("    [0:02] Turn 2: read_file(b.py)")
    rendered = out.getvalue()
    assert rendered.count("\x1b[1A") == 0


def test_reset_prevents_overwriting_another_writers_output():
    out = io.StringIO()
    renderer = ProgressRenderer(stream=out, color=True)
    renderer("    [0:01] Turn 1: read_file(a.py)")
    renderer.reset()
    renderer("    [0:02] Turn 2: read_file(b.py)")
    assert "\x1b[1A" not in out.getvalue()


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

