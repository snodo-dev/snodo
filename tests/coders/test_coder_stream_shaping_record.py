"""The record stays raw while the live view takes the validator's shape (#306).

End-to-end through ``SubprocessCoderAdapter`` with real processes:

- the shaped coder line appears only in the interactive render — the capture
  (``CompletedProcess.stdout``/``stderr``), a job's stdout.log (the plain,
  non-tty render of the same lines) and the diagnostic tail that becomes a
  halt payload's ``output_tail`` are byte-identical to before the shaping;
- an unrecognised line still reaches the operator, verbatim;
- a shaping sink that raises cannot kill the run (the swallow-and-log
  behaviour of ``_emit_coder_line`` is inherited, not replaced);
- recognition is presentation: it adds no files, no metadata, no fact the
  engine acts on (ADR 034).
"""

import io
import subprocess
import sys
from pathlib import Path

import pytest

from snodo.coders.subprocess_adapter import SubprocessCoderAdapter
from snodo.core.interfaces import TaskSpec
from snodo.engine.progress import ProgressRenderer

from tests.coders.test_subprocess_streaming import _ScriptedAdapter, _script

# A coder CLI that narrates, states two tool actions, emits noise no pattern
# claims, and leaves a reason on stderr — the shapes seen across host CLIs.
_CLI_NARRATION = """
import sys

print("Sure — let me set up my plan.", flush=True)
print("Read src/app.tsx", flush=True)
print("$ npm test", flush=True)
print("unrecognised chatter", flush=True)
sys.stderr.write("warn: deprecated config\\n")
sys.stderr.flush()
"""

_RAW_STDOUT = (
    "Sure — let me set up my plan.\n"
    "Read src/app.tsx\n"
    "$ npm test\n"
    "unrecognised chatter\n"
)
_RAW_STDERR = "warn: deprecated config\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
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
    return root


def _adapter(repo: Path, tmp_path: Path, body: str, name: str) -> SubprocessCoderAdapter:
    script = _script(tmp_path, name, body)
    adapter = _ScriptedAdapter(workspace=repo, timeout_seconds=60)
    adapter._argv = [sys.executable, "-u", str(script)]
    return adapter


class TestLiveViewShapedRecordRaw:
    def test_capture_is_byte_identical_while_the_view_is_shaped(self, repo, tmp_path):
        adapter = _adapter(repo, tmp_path, _CLI_NARRATION, "narrating_cli")
        live = io.StringIO()
        renderer = ProgressRenderer(stream=live, color=True, window=20)
        # The engine's wiring: the same sink carries the dispatch boundary and
        # the coder's raw lines, and the renderer shapes between them (#306).
        adapter.progress_callback = renderer
        renderer("  Coder dispatched")

        result = adapter._run_subprocess(adapter._argv, str(repo))
        renderer("  Coder returned (0 artifact(s))")

        # The record: the coder's bytes exactly as written, no turn numbers,
        # no composition — nothing here changed for the shaping.
        assert result.stdout == _RAW_STDOUT
        assert result.stderr == _RAW_STDERR
        # The live view: recognised lines carry elapsed time, a turn count
        # and what the turn did; the prose and the stderr note pass through.
        visible = renderer.visible_lines()
        shaped = [v for v in visible if "Turn" in v]
        assert len(shaped) == 2
        assert shaped[0].endswith("Turn 1: read_file(src/app.tsx)")
        assert shaped[1].endswith("Turn 2: run(npm test)")
        assert "Sure — let me set up my plan." in visible
        assert "unrecognised chatter" in visible
        assert "warn: deprecated config" in visible

    def test_a_plain_stream_writes_the_record_byte_for_byte(self, repo, tmp_path):
        """The job's stdout.log path: colour off, every coder line verbatim."""
        adapter = _adapter(repo, tmp_path, _CLI_NARRATION, "narrating_cli")
        log = io.StringIO()
        adapter.progress_callback = ProgressRenderer(stream=log, color=False)

        adapter._run_subprocess(adapter._argv, str(repo))

        written = log.getvalue().splitlines()
        assert "Read src/app.tsx" in written
        assert "$ npm test" in written
        assert not any("Turn" in line for line in written)
        # Each of the coder's stdout lines lands exactly once, unaltered.
        for raw in _RAW_STDOUT.splitlines():
            assert written.count(raw) == 1


class TestHaltPayloadTailUnchanged:
    def test_output_tail_is_the_raw_stream_ending_not_the_shaped_view(self, repo, tmp_path):
        adapter = _adapter(repo, tmp_path, _CLI_NARRATION, "narrating_cli")
        live = io.StringIO()
        adapter.progress_callback = ProgressRenderer(stream=live, color=True, window=20)

        artifact = adapter.implement(TaskSpec(description="no-op", constraints=[]))

        # The tail is built from the capture, not the live view: the labelled
        # two-stream form, with the coder's own words byte-for-byte.
        tail = artifact.metadata["output_tail"]
        assert tail == f"[stdout]\n{_RAW_STDOUT.strip()}\n\n[stderr]\n{_RAW_STDERR.strip()}"
        assert "Turn" not in tail
        # Shaping added no fact the engine can act on (ADR 034): same empty
        # work set, same flags a recognising adapter never touched.
        assert artifact.files == []
        assert adapter.last_timed_out is False


class TestSinkFailuresStillCannotKillTheRun:
    def test_a_sink_that_raises_on_a_recognised_line_is_still_swallowed(self, repo, tmp_path):
        adapter = _adapter(repo, tmp_path, _CLI_NARRATION, "narrating_cli")
        seen: list[str] = []

        def exploding_sink(line: str) -> None:
            seen.append(line)
            raise RuntimeError("sink exploded")

        adapter.progress_callback = exploding_sink
        artifact = adapter.implement(TaskSpec(description="no-op", constraints=[]))

        # The first failure is swallowed per line by _emit_coder_line; the run
        # completes and its record survives.
        assert "Read src/app.tsx" in seen
        assert artifact.metadata["output_tail"] == (
            f"[stdout]\n{_RAW_STDOUT.strip()}\n\n[stderr]\n{_RAW_STDERR.strip()}"
        )
