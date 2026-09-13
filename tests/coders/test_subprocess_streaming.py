"""Live observability tests for SubprocessCoderAdapter's output streaming.

FILE: tests/coders/test_subprocess_streaming.py

The point of these tests is timing, not just content: output a subprocess
writes *before* it exits must reach the caller's progress sink *before* it
exits. A test that only inspects the final captured text would have passed
against the old single-blocking-``communicate()`` implementation and proved
nothing, so the fixtures pause mid-run and the assertions are made during the
pause (via the callback firing while ``_run_subprocess`` has not returned).

Also covered: the stderr-flood deadlock the old ``communicate()`` protected
against (both pipes must be drained concurrently or a talkative coder hangs),
and the pre-existing capture promises (full-text return, timeout partial
output, stderr-only diagnostic tail).
"""

import subprocess
import sys
import time
from pathlib import Path

import pytest

from snodo.coders.subprocess_adapter import SubprocessCoderAdapter
from snodo.core.interfaces import TaskSpec

# Child waits on a gate file the parent only creates from inside the progress
# callback. If streaming is broken the gate never appears, the child gives up
# after GATE_DEADLINE and exits non-zero — the test then fails on the exit
# code instead of hanging for the adapter's full timeout.
GATE_DEADLINE_SECONDS = 20

_NARRATOR = """
import pathlib
import sys
import time

gate = pathlib.Path(sys.argv[1])
print("FIRST-LINE", flush=True)
deadline = time.monotonic() + {deadline}
while not gate.exists() and time.monotonic() < deadline:
    time.sleep(0.05)
if not gate.exists():
    print("GATE-TIMEOUT", flush=True)
    sys.exit(3)
print("SECOND-LINE", flush=True)
""".format(deadline=GATE_DEADLINE_SECONDS)

_STDERR_FLOOD = """
import pathlib
import sys
import time

sys.stderr.write("E" * 262144 + "\\n")
sys.stderr.flush()
print("OUT-MARKER", flush=True)
gate = pathlib.Path(sys.argv[1])
deadline = time.monotonic() + {deadline}
while not gate.exists() and time.monotonic() < deadline:
    time.sleep(0.05)
sys.exit(0 if gate.exists() else 3)
""".format(deadline=GATE_DEADLINE_SECONDS)

_STALLER = """
import sys
import time

print("PREFLIGHT", flush=True)
sys.stderr.write("STALL-REASON\\n")
sys.stderr.flush()
time.sleep(120)
"""

_STDERR_ONLY = """
import sys

sys.stderr.write("rate limit reached\\n")
sys.stderr.flush()
"""


class _ScriptedAdapter(SubprocessCoderAdapter):
    """Subprocess adapter whose 'CLI' is a canned argv supplied by the test."""

    coder_name: str = "scripted-test"
    binary: str = "scripted-test"
    model_prefix: str = "scripted-test/"
    install_hint: str = "n/a — test adapter"

    _argv: list[str] = []

    def _build_argv(self, prompt: str, project_root: str, model: str) -> list[str]:
        return list(self._argv)


def _script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"{name}.py"
    path.write_text(body)
    return path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


class TestMidRunObservability:
    def test_output_is_observable_before_the_process_exits(self, tmp_path, workspace):
        """Lines reach the sink during the child's mid-run pause, not after.

        The child writes one line, pauses until a gate file appears, then
        writes a second line and exits. The gate is opened *inside* the
        progress callback, so a visible SECOND-LINE is proof that the callback
        ran while the child was still alive; the recorded ``in_flight`` flag is
        proof that ``_run_subprocess`` itself had not returned yet.
        """
        script = _script(tmp_path, "narrator", _NARRATOR)
        gate = tmp_path / "gate"
        adapter = _ScriptedAdapter(workspace=workspace, timeout_seconds=60)

        observed: list[tuple[str, bool]] = []
        state = {"in_flight": False}

        def cb(line: str) -> None:
            observed.append((line, state["in_flight"]))
            if line == "FIRST-LINE":
                gate.touch()

        adapter.progress_callback = cb
        argv = [sys.executable, "-u", str(script), str(gate)]

        state["in_flight"] = True
        result = adapter._run_subprocess(argv, str(workspace))
        state["in_flight"] = False

        assert result.returncode == 0, "child gave up: output never surfaced mid-run"
        assert ("FIRST-LINE", True) in observed
        assert ("SECOND-LINE", True) in observed
        # Nothing is lost from the capture either way.
        assert result.stdout == "FIRST-LINE\nSECOND-LINE\n"

    def test_stderr_flood_does_not_deadlock_the_run(self, tmp_path, workspace):
        """A coder writing more to stderr than a pipe holds must not hang.

        The child dumps 256 KB to stderr — four times a typical 64 KB pipe
        buffer — before it will ever touch stdout. A reader that drains only
        stdout (or drains the streams serially) lets stderr fill, the child
        blocks in its write, and neither side can finish. Here both pipes are
        drained concurrently, so OUT-MARKER still arrives mid-run.
        """
        script = _script(tmp_path, "flooder", _STDERR_FLOOD)
        gate = tmp_path / "gate"
        adapter = _ScriptedAdapter(workspace=workspace, timeout_seconds=30)

        observed: list[tuple[str, bool]] = []
        state = {"in_flight": False}

        def cb(line: str) -> None:
            observed.append((line, state["in_flight"]))
            if line == "OUT-MARKER":
                gate.touch()

        adapter.progress_callback = cb
        argv = [sys.executable, "-u", str(script), str(gate)]

        state["in_flight"] = True
        started = time.monotonic()
        result = adapter._run_subprocess(argv, str(workspace))
        state["in_flight"] = False

        assert result.returncode == 0, "run deadlocked or the flood never drained"
        assert time.monotonic() - started < GATE_DEADLINE_SECONDS
        assert ("OUT-MARKER", True) in observed
        assert result.stderr == "E" * 262144 + "\n"

    def test_streaming_choice_belongs_to_the_caller(self, tmp_path, workspace):
        """No progress_callback: silent full capture, exactly the old promise."""
        script = _script(tmp_path, "narrator", _NARRATOR)
        (tmp_path / "gate").touch()  # child never pauses; gate already open
        adapter = _ScriptedAdapter(workspace=workspace, timeout_seconds=60)
        assert getattr(adapter, "progress_callback", None) is None

        argv = [sys.executable, "-u", str(script), str(tmp_path / "gate")]
        result = adapter._run_subprocess(argv, str(workspace))

        assert result.returncode == 0
        assert result.stdout == "FIRST-LINE\nSECOND-LINE\n"
        assert result.stderr == ""


class TestTimeoutPromisesSurviveStreaming:
    def test_timeout_raises_with_partial_output_that_was_also_streamed(
        self, tmp_path, workspace
    ):
        """A stalled run is still killed on schedule, with its output recorded.

        The partial output is often the only record of why a run stalled, so
        it must survive into the raised TimeoutExpired — and it must have
        reached the sink while the child was still alive, not only at the end.
        """
        script = _script(tmp_path, "staller", _STALLER)
        adapter = _ScriptedAdapter(workspace=workspace, timeout_seconds=3)

        seen: list[str] = []
        adapter.progress_callback = seen.append

        argv = [sys.executable, "-u", str(script)]
        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired) as exc_info:
            adapter._run_subprocess(argv, str(workspace))

        assert "PREFLIGHT" in exc_info.value.output
        assert "STALL-REASON" in exc_info.value.stderr
        # The two lines arrive on independent reader threads, so their
        # relative order is not defined — only that both were streamed.
        assert sorted(seen) == ["PREFLIGHT", "STALL-REASON"]
        # The process group was killed, not waited out: the child slept for
        # 120s and the call returned on the 3s boundary instead.
        assert time.monotonic() - started < 20


class TestDiagnosticTailStillFedByTheStream:
    def test_stderr_only_run_still_yields_verbatim_stderr_tail(
        self, tmp_path, workspace
    ):
        """The stderr-only tail (itself a past bug fix) must not regress.

        Driven through the whole ``implement()`` path with a real process:
        output arriving on the stderr reader thread must land in the same
        capture the combined-tail logic consumes, untouched and unlabeled.
        """
        root = tmp_path / "repo"
        root.mkdir()
        for cmd in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "test@test.com"],
            ["git", "config", "user.name", "Test"],
        ):
            subprocess.run(cmd, cwd=root, check=True)
        (root / "README.md").write_text("# Test Workspace\n")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)

        script = _script(tmp_path, "stderr_only", _STDERR_ONLY)
        adapter = _ScriptedAdapter(workspace=root, timeout_seconds=60)
        adapter._argv = [sys.executable, "-u", str(script)]

        streamed: list[str] = []
        adapter.progress_callback = streamed.append

        artifact = adapter.implement(TaskSpec(description="no-op", constraints=[]))

        assert artifact.files == []
        assert artifact.metadata["output_tail"] == "rate limit reached"
        assert streamed == ["rate limit reached"]
