"""Progress formatting and turn-observability helpers.

FILE: snodo/engine/progress.py (Issue #51, #294, #300)

Surfaces real-time LLM tool-loop progress (elapsed time and tool turns) for
coders and validators without visual noise or control characters.

Presentation (Issue #294): the lines the engine already emits are classified by
their shape — a phase boundary, a validator/coder tool turn, a validator's
started/finished activity, a gate verdict, a terminal halt — and rendered with
colour on an interactive terminal. This is decoration over information the sink
already carries: it adds no state, no severity, no halt type and no telemetry,
and it never changes what is emitted, when, or what reaches the audit log. When
colour is unavailable (a non-tty, a pipe, ``NO_COLOR``) every line is printed
verbatim, byte-identical to the sink's pre-#294 output.

Windowed live view (Issue #300): #294 collapsed a colour-enabled run to one
overwritten turn line, so an operator watching several concurrent validators
could see only the latest read and lost every verdict and phase boundary the
moment the next turn arrived. The renderer now holds a scrolling window of
recent lines instead of a single row: only *identical* consecutive turn lines
collapse (a judge repeating the same read), so a verdict, phase boundary or
halt stays on screen until the window itself scrolls past it.

Coder stream in the same shape (Issue #306): a validator's turn reaches the
sink already composed — "[0:28] Turn 5: read_file(...)" — while a subprocess
coder's reaches it as raw CLI output, so one sink carried two languages. On
the interactive path only, the renderer now recognises what a coder line
states it did (a read, an edit, a command run) and re-emits it as
"[0:28] Turn 3: read_file(src/app.tsx)". Recognition is best-effort: an
unrecognised line passes through verbatim rather than being dropped or
guessed at, nothing here is decision input (ADR 034), and the record stays
raw — the capture lists, a job's stdout.log and any non-tty or NO_COLOR
stream all still see the coder's bytes exactly as written.
"""

import json
import os
import re
import shutil
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple


class ProgressSink:
    """Guard around an operator-facing callback (progress narration or verdict).

    A sink is an observer: a bug in it must not kill the work it watches, and
    it must not fail silently either. The first failure is reported once on
    stderr — where an operator watching the run will see it — and every later
    call is dropped, so one broken sink cannot flood the terminal or slow the
    loop. This is the single place a callback is made safe; call sites never
    wrap their own ``try/except`` and never guess which callback they hold.
    """

    def __init__(self, callback: Any, label: str = "progress") -> None:
        self._callback = callback
        self._label = label
        self._failed = False

    def __call__(self, *args: Any) -> None:
        if self._callback is None or self._failed:
            return
        try:
            self._callback(*args)
        except Exception as exc:  # noqa: BLE001 — an observer must not kill its subject
            self._failed = True
            print(
                f"[snodo] {self._label} sink failed and is now suppressed for "
                f"the rest of the run: {type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )


def ensure_progress_sink(callback: Any, label: str = "progress") -> Optional[ProgressSink]:
    """Wrap *callback* in a :class:`ProgressSink`, unless it already is one.

    Returning the existing sink keeps its "reported once" state shared across
    every emitter instead of resetting it at each call site.
    """
    if callback is None:
        return None
    if isinstance(callback, ProgressSink):
        return callback
    return ProgressSink(callback, label)


def format_elapsed(seconds: float) -> str:
    """Format elapsed seconds into m:ss (e.g. 0:04, 1:12), hours past an hour.

    The turn labels the engine composes ("[0:28] Turn 5: ...") are minutes
    deep at most in practice, but a coder phase or a long watch can pass an
    hour, and "60:05" makes the reader do the division. Past an hour the same
    reading as a clock applies: 1:00:05 (#316).
    """
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if hours:
        return f"{hours}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_duration(seconds: float) -> str:
    """Render a duration the way it reads: "12.5s", "22:35", "1:04:11".

    The single renderer every shown duration goes through (#316): seconds
    while it is seconds, m:ss once it passes a minute, h:mm:ss once it passes
    an hour. "in 1355.5s" forced the reader to convert a number that is
    really "22:35"; four call sites each holding their own format string is
    four chances to drift, so they hold one call instead.
    """
    secs = max(0.0, float(seconds))
    if secs < 60:
        return f"{secs}s"
    return format_elapsed(secs)


def format_tool_call_summary(tool_calls: Optional[List[Any]]) -> str:
    """Format a list of tool call objects into a clean, human-readable turn summary."""
    if not tool_calls:
        return "(no tools called)"

    formatted = []
    for tc in tool_calls:
        func = getattr(tc, "function", None)
        name = getattr(func, "name", None) or getattr(tc, "name", "unknown_tool")
        raw_args = getattr(func, "arguments", None) or getattr(tc, "arguments", {})

        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except Exception:
                args = {}
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            args = {}

        if name == "read_file":
            path = args.get("path", "")
            formatted.append(f"read_file({path})")
        elif name == "read_files":
            paths = args.get("paths", [])
            if isinstance(paths, list):
                paths_str = ", ".join(str(p) for p in paths)
                formatted.append(f"read_files({paths_str})")
            else:
                formatted.append(f"read_files({paths})")
        elif name == "read_file_lines":
            path = args.get("path", "")
            start = args.get("start", "")
            end = args.get("end", "")
            formatted.append(f"read_file_lines({path}:{start}-{end})")
        elif name == "list_files":
            directory = args.get("directory", ".")
            formatted.append(f"list_files({directory})")
        elif name == "submit_files":
            files = args.get("files", [])
            count = len(files) if isinstance(files, list) else ""
            formatted.append(f"submit_files({count} file(s))" if count != "" else "submit_files")
        elif name == "submit_verdict":
            sev = args.get("severity", "")
            formatted.append(f"submit_verdict({sev})" if sev else "submit_verdict")
        else:
            # General fallback for any other tool
            first_arg = next((str(v) for k, v in args.items() if isinstance(v, (str, int))), "")
            formatted.append(f"{name}({first_arg})" if first_arg else f"{name}")

    return ", ".join(formatted)


# ── Presentation (Issue #294) ──────────────────────────────────────────
#
# The engine already emits every kind of line this renders; classification is
# by the shape of the line the sink carries, never by new telemetry. Colour and
# in-place compaction are decoration for an interactive terminal only: with
# colour unavailable (non-tty, a pipe, NO_COLOR) a line is printed verbatim,
# byte-identical to the sink's pre-#294 output, and what is logged or audited is
# untouched. Raw ANSI is used rather than ``rich`` so no package dependency is
# added to the engine; the dashboard has its own presentation and is not routed
# through here.

#: The presentation kinds the engine's own lines fall into.
TURN = "turn"
PHASE = "phase"
HALT = "halt"
CODER = "coder"
GATE_OK = "gate_ok"
GATE_WARN = "gate_warn"
GATE_FAIL = "gate_fail"
ACTIVITY = "activity"
PLAIN = "plain"

_RESET = "\x1b[0m"
_DIM = "\x1b[2m"
_BOLD = "\x1b[1m"
_RED = "\x1b[31m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_CYAN = "\x1b[36m"
_CLEAR_EOL = "\x1b[K"

#: Explicit override for the live window's row count (Issue #300), so an
#: operator whose terminal is unusually short or tall can reach a size the
#: auto-detected default would not offer.
_WINDOW_ENV = "SNODO_WATCH_WINDOW"
#: Rows of headroom left below the window for the shell prompt / next command.
_RESERVED_ROWS = 4
#: Floor so a very short terminal still shows more than one line of history.
_MIN_WINDOW = 5
#: Used only when the terminal size cannot be determined at all.
_FALLBACK_TERMINAL_LINES = 24

#: A tool-loop turn line, the one kind that compacts: "    [0:14] Turn 9: ...".
#: The optional leading group carries the hours bucket a run past an hour now
#: renders ("[1:04:11]"), so such a turn still reads as a turn (#316).
_TURN_RE = re.compile(r"^\s*\[(?:\d+:)?\d+:\d{2}\]\s+Turn\s+\d+:")
#: Phase boundaries: entering a quorum, post-validation, a spec rewrite.
_PHASE_RES = (
    re.compile(r"^\s*Validating \(pre-execute\):"),
    re.compile(r"^\s*Post-validating:"),
    re.compile(r"^\s*Spec authored"),
)
#: Terminal outcomes the loop narrates itself: the recovery halts.
_HALT_RES = (
    re.compile(r"Recovery depth exhausted"),
    re.compile(r"Recovery stalled"),
    re.compile(r"Recovery premise stale"),
    re.compile(r"halting loop"),
    re.compile(r"halting instead of dispatching"),
)
#: Coder / execution activity and recovery dispatch.
_CODER_RES = (
    re.compile(r"^\s*Coder dispatched"),
    re.compile(r"^\s*Coder returned"),
    re.compile(r"^\s*Coder timed out"),
    re.compile(r"^\s*Coder exhausted"),
    re.compile(r"^\s*Prepared environment:"),
    re.compile(r"^\s*Recovery \(attempt"),
)
#: A per-validator verdict line ("    ✓ id: ...", "    ❌ id: ..."). The warning
#: marker carries a U+FE0F variation selector after U+26A0.
_GATE_RE = re.compile(r"^\s*[✓⚠❌💥]\ufe0f?\s*\S+:")
#: A validator going out or coming back, and the spec-rewrite detail lines.
_ACTIVITY_RES = (
    re.compile(r"^\s*\S+:\s+started\s*$"),
    re.compile(r"^\s*\S+:\s+finished\s*$"),
    re.compile(r"^\s+(Original|Authored|Critique):"),
)

_KIND_STYLE = {
    PHASE: _BOLD + _CYAN,
    TURN: _DIM,
    CODER: _CYAN,
    GATE_OK: _GREEN,
    GATE_WARN: _YELLOW,
    GATE_FAIL: _RED,
    HALT: _BOLD + _RED,
    ACTIVITY: _DIM,
    PLAIN: "",
}


def classify_progress_line(line: str) -> str:
    """Return the presentation kind of an engine progress line.

    Classification reads only the shape of the line the engine already emits —
    it is a rendering decision, not telemetry, and adds no state or vocabulary.
    """
    if _TURN_RE.match(line):
        return TURN
    if any(pattern.match(line) for pattern in _PHASE_RES):
        return PHASE
    if any(pattern.search(line) for pattern in _HALT_RES):
        return HALT
    if _GATE_RE.match(line):
        marker = line.lstrip()[:1]
        if marker == "✓":
            return GATE_OK
        if marker == "⚠":
            return GATE_WARN
        return GATE_FAIL
    if any(pattern.match(line) for pattern in _CODER_RES):
        return CODER
    if any(pattern.match(line) for pattern in _ACTIVITY_RES):
        return ACTIVITY
    return PLAIN


def color_enabled(stream: Any = None) -> bool:
    """Return True only when colour should be used for *stream*.

    ``NO_COLOR`` (non-empty, per no-color.org) disables, so a piped or
    redirected stream and a caller that opts out both get plain output.
    """
    if os.environ.get("NO_COLOR"):
        return False
    stream = stream if stream is not None else sys.stdout
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def default_window_size(stream: Any = None) -> int:
    """Return how many recent lines the live window should hold (Issue #300).

    ``SNODO_WATCH_WINDOW`` always wins when set, so an operator whose
    terminal is unusually short or tall can still reach a size the
    auto-detected default would not offer. Otherwise the window scales with
    *stream*'s actual current height rather than a hard-coded constant,
    leaving :data:`_RESERVED_ROWS` of headroom for the shell prompt, with a
    floor so a very short terminal still shows more than one line.
    """
    override = os.environ.get(_WINDOW_ENV)
    if override:
        try:
            n = int(override)
        except ValueError:
            n = 0
        if n > 0:
            return n

    stream = stream if stream is not None else sys.stdout
    try:
        lines = os.get_terminal_size(stream.fileno()).lines
    except Exception:
        lines = shutil.get_terminal_size(fallback=(80, _FALLBACK_TERMINAL_LINES)).lines
    return max(_MIN_WINDOW, lines - _RESERVED_ROWS)


def render_progress_line(line: str, color: bool) -> str:
    """Return *line* styled for display, or verbatim when colour is off.

    Pure and stateless: the same classification drives both this line renderer
    and the stream renderer below, so a line styled here and one styled in a
    live run always agree.
    """
    if not color:
        return line
    style = _KIND_STYLE.get(classify_progress_line(line), "")
    return f"{style}{line}{_RESET}" if style else line


# ── Coder stream shaping (Issue #306) ──────────────────────────────────
#
# A subprocess coder forwards its own stdout/stderr verbatim, so its live
# stream reads as a firehose where the validator stream reads as numbered
# turns. These patterns recognise, from the shape of a line the coder
# already printed, the tool action it states — read, write, edit, delete,
# run, list, search — so the renderer can re-emit it in the validator's
# shape. Recognition is deliberately narrow (a leading verb plus a
# concrete target): coders differ in what they emit and a line that cannot
# be recognised is passed through rather than guessed at. The result is
# presentation only: it never reaches the capture lists, a job's
# stdout.log or a halt payload, and no engine decision reads it (ADR 034).

#: A CLI bullet/checkbox before the verb: "✔ Read", "→  edit", "- Listing".
_CODER_LEAD_RE = re.compile(r"^[\s\-–—*•·→➜›»|✔✓✅☑☒●○◦■□▲△%!]+")
#: A shell prompt echo: "$ make build" states a command run.
_CODER_SHELL_RE = re.compile(r"^\s*\$\s+(?P<target>\S.*?)\s*$")

_CODER_TOOL_RES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("read_file", re.compile(
        r"^(?:read|reads|reading|open(?:s|ed|ing)?|view(?:s|ed|ing)?|cat)\s+(?:the\s+)?(?:file\s+)?[:\-]?\s*(?P<target>\S.*?)\s*$",
        re.IGNORECASE)),
    ("write_file", re.compile(
        r"^(?:writ(?:e|es|ing)|wrote|creat(?:e|es|ed|ing))\s+(?:the\s+)?(?:file\s+)?(?:module\s+)?[:\-]?\s*(?P<target>\S.*?)\s*$",
        re.IGNORECASE)),
    ("edit_file", re.compile(
        r"^(?:edit(?:s|ed|ing)?|updat(?:e|es|ed|ing)|patch(?:es|ed|ing)?|appl(?:y|ies|ied|ying)|modif(?:y|ies|ied|ying))\s+(?:the\s+)?(?:file\s+)?[:\-]?\s*(?P<target>\S.*?)\s*$",
        re.IGNORECASE)),
    ("delete_file", re.compile(
        r"^(?:delet(?:e|es|ed|ing)|remov(?:e|es|ed|ing))\s+(?:the\s+)?(?:file\s+)?[:\-]?\s*(?P<target>\S.*?)\s*$",
        re.IGNORECASE)),
    ("run", re.compile(
        r"^(?:run(?:s|ning)?|execut(?:e|es|ed|ing)|bash|shell|command|test(?:s|ing)?)\s*[:\-]?\s+(?P<target>\S.*?)\s*$",
        re.IGNORECASE)),
    ("list_files", re.compile(
        r"^(?:list(?:s|ed|ing)?|ls|tree|scan(?:s|ned|ning)?|brows(?:e|es|ed|ing))\s+(?:the\s+)?(?:dir(?:ector)?y|files?|contents?|tree)?\s*[:\-]?\s*(?P<target>\S.*?)\s*$",
        re.IGNORECASE)),
    ("search", re.compile(
        r"^(?:search(?:es|ed|ing)?|fin(?:d|ds|ding|ed)|grep(?:s|ped|ping)?|rg|glob(?:s|bed|bing)?|lookup)\s*[:\-]?\s+(?:for\s+)?(?P<target>\S.*?)\s*$",
        re.IGNORECASE)),
)

#: A file-operation target must at least look like a path or a named file,
#: not prose: "Read the manifest carefully" stays a prose line, "Read
#: src/app.tsx" is a turn. Commands and search patterns carry no such anchor.
_CODER_PATHISH_RE = re.compile(
    r"(/|~|\.(?:py|ts|tsx|js|jsx|json|md|rs|go|toml|ya?ml|sh|css|html?|txt|cfg|ini|rb|java|c|cc|cpp|h|lock|sql|xml|env|gitignore|dockerfile)$)",
    re.IGNORECASE,
)
#: Width cap so one long path cannot dictate the live line's shape.
_CODER_TARGET_MAX = 60


def _clean_coder_target(target: str) -> str:
    """Strip quoting and trailing sentence punctuation from a target."""
    target = target.strip().strip("\"'`“”‘’")
    return target.rstrip(".,;:!?)]}”’\"'`").strip()


def summarize_coder_line(line: str) -> Optional[str]:
    """Return what a raw coder output line states it did, or None.

    Mirrors the vocabulary of :func:`format_tool_call_summary` so coder and
    validator turns read the same way ("read_file(src/app.tsx)"). None means
    unrecognised — the caller must pass the line through untouched; it is
    better to show an operator a raw line than to mislabel it.
    """
    shell = _CODER_SHELL_RE.match(line)
    if shell:
        return f"run({_shorten_coder_target(_clean_coder_target(shell.group('target')))})"

    stripped = _CODER_LEAD_RE.sub("", line, count=1)
    for name, pattern in _CODER_TOOL_RES:
        match = pattern.match(stripped)
        if not match:
            continue
        target = _clean_coder_target(match.group("target"))
        if not target:
            continue
        if name in ("read_file", "write_file", "edit_file", "delete_file", "list_files"):
            first = target.split()[0]
            if not _CODER_PATHISH_RE.search(first):
                continue
            target = first
        return f"{name}({_shorten_coder_target(target)})"
    return None


def _shorten_coder_target(target: str) -> str:
    return target if len(target) <= _CODER_TARGET_MAX else target[: _CODER_TARGET_MAX - 3] + "..."


class ProgressRenderer:
    """Render engine progress lines to a stream, as a scrolling window.

    The single presentation choke point for the engine's progress sink. When
    colour is unavailable — the default on a pipe, a redirected stream, or under
    ``NO_COLOR`` — every line is written verbatim, byte-identical to the sink's
    pre-#294 output.

    When colour is available the line is styled by kind and held in a window of
    the last *window* lines (Issue #300): the whole window is repainted on each
    call, scrolling as new lines arrive, so a verdict, phase boundary or halt
    stays readable until the window itself scrolls past it rather than being
    replaced by the very next turn. Only an *identical* consecutive turn line —
    a judge repeating the same read — collapses into the row it repeats,
    counted rather than each claiming a row of its own; any other line, turn or
    not, is a distinct event and gets its own row. On this path only, a raw
    coder line whose shape states what the coder did is first composed into a
    turn line, so the coder's live stream reads like a validator's (Issue #306).

    Compaction is presentation only: it never suppresses a line from the log or
    the audit trail, both of which receive the plain text. A writer that shares
    stdout with this renderer calls :meth:`reset` first so a following line is
    not painted over its output. Safe to call from the validator thread pool.
    """

    def __init__(
        self,
        stream: Any = None,
        color: Optional[bool] = None,
        window: Optional[int] = None,
    ) -> None:
        self._stream = stream
        self._color = color
        self._window = window
        self._lock = threading.Lock()
        self._rows: List[Dict[str, Any]] = []
        self._drawn = 0
        # Coder-phase cursor (Issue #306): set when a "Coder dispatched" line
        # passes through the colour path, cleared when the coder's turn at the
        # sink ends. Presentation-local bookkeeping — elapsed time and a turn
        # count for shaping the coder's live stream — not engine state: the
        # engine's decisions never read it (ADR 034).
        self._coder_phase: Optional[Dict[str, Any]] = None

    def _use_color(self) -> bool:
        if self._color is not None:
            return self._color
        return color_enabled(self._stream)

    def _window_size(self) -> int:
        if self._window is not None:
            return self._window
        return default_window_size(self._stream)

    def __call__(self, line: str) -> None:
        with self._lock:
            self._emit(line)

    def reset(self) -> None:
        """Forget the painted window, so the next line starts a fresh one.

        For a writer that shares stdout with this renderer: without this the
        next line would move the cursor up and paint over that writer's output.
        """
        with self._lock:
            self._rows = []
            self._drawn = 0

    def _emit(self, line: str) -> None:
        stream = self._stream if self._stream is not None else sys.stdout
        if not self._use_color():
            print(line, file=stream, flush=True)
            return

        kind = classify_progress_line(line)
        line, kind = self._shape_coder_line(line, kind)
        last = self._rows[-1] if self._rows else None

        if kind == TURN and last is not None and last["kind"] == TURN and last["text"] == line:
            last["count"] += 1
        else:
            self._rows.append({"text": line, "kind": kind, "count": 1})
            window = max(1, self._window_size())
            del self._rows[:-window]

        self._repaint(stream)

    #: The coder-phase boundaries carried by the sink itself (#306): the
    #: engine emits the dispatch line before the subprocess runs and one of
    #: the closing lines when it ends, so the renderer needs no new signal —
    #: only the lines every viewer already sees.
    _CODER_DISPATCH_RE = re.compile(r"^\s*Coder dispatched\b")

    def _shape_coder_line(self, line: str, kind: str) -> Tuple[str, str]:
        """Compose a recognised coder line into the validator's turn shape.

        Only the colour (interactive) path calls this: the capture, a job's
        stdout.log and every plain stream keep the coder's raw bytes. While
        a coder owns the sink, a line whose shape states what the coder did
        (``summarize_coder_line``) is re-emitted as
        "    [m:ss] Turn N: <summary>" — the same line a validator's turn
        makes, so it classifies as TURN and compacts the same way. An
        unrecognised line is returned untouched: passing it through beats
        guessing, and nothing is ever dropped. Shaping a line here cannot
        affect the run: this is the last stop before the terminal, the
        return value feeds only the window, and the engine never reads it
        back (ADR 034).
        """
        if kind == CODER:
            if self._CODER_DISPATCH_RE.match(line):
                self._coder_phase = {"started": time.monotonic(), "turns": 0}
            else:
                self._coder_phase = None
            return line, kind
        if kind in (PHASE, HALT):
            # Another engine phase took the sink back; the coder's stream is over.
            self._coder_phase = None
            return line, kind
        if self._coder_phase is None or kind == TURN:
            # Outside a coder phase, or already a composed turn line (a
            # litellm coder reports its own turns — never double-shape).
            return line, kind
        summary = summarize_coder_line(line)
        if summary is None:
            return line, kind
        self._coder_phase["turns"] += 1
        elapsed = format_elapsed(time.monotonic() - self._coder_phase["started"])
        return f"    [{elapsed}] Turn {self._coder_phase['turns']}: {summary}", TURN

    @staticmethod
    def _format_row(row: Dict[str, Any]) -> str:
        text = row["text"]
        return f"{text}  (×{row['count']})" if row["count"] > 1 else text

    def _repaint(self, stream: Any) -> None:
        if self._drawn:
            stream.write(f"\x1b[{self._drawn}A")
        for row in self._rows:
            style = _KIND_STYLE.get(row["kind"], "")
            text = self._format_row(row)
            stream.write("\r" + _CLEAR_EOL)
            if style:
                stream.write(f"{style}{text}{_RESET}\n")
            else:
                stream.write(f"{text}\n")
        stream.flush()
        self._drawn = len(self._rows)

    def visible_lines(self) -> List[str]:
        """Return the plain text currently held in the window, oldest first.

        A read-only seam onto what the window currently shows, without having
        to parse the painted ANSI output.
        """
        with self._lock:
            return [self._format_row(row) for row in self._rows]
