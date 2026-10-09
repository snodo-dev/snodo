"""Contract tests for the reusable CLI wave indicator (Fixes #780)."""

import io
import os
import pty
import select
import subprocess
import sys
import time

import pytest
from rich.console import Console
from rich.table import Table

from snodo.cli.spinner import WaveSpinner, wave_frame, wave_while_silent


class Terminal(io.StringIO):
    """In-memory terminal stream with UTF-8 encoding."""

    encoding = "utf-8"

    def isatty(self) -> bool:
        return True


def test_wave_frame_follows_staggered_cycle() -> None:
    cycle = "▁▂▃▄▅▆▇█▇▆▅▄▃▂"
    for index in range(len(cycle)):
        assert (
            wave_frame(index)
            == "["
            + "".join(
                cycle[(index + offset) % len(cycle)] for offset in (0, 2, 4, 6, 8)
            )
            + "]"
        )


def test_non_tty_writes_to_neither_stream(capsys) -> None:
    with WaveSpinner("working", delay=0, interval=0):
        time.sleep(0.01)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize(
    "name,value",
    [
        ("CI", "true"),
        ("NO_COLOR", ""),
        ("TERM", "dumb"),
        ("SNODO_NO_SPINNER", "1"),
    ],
)
def test_environment_disables_spinner(monkeypatch, name: str, value: str) -> None:
    stream = Terminal()
    monkeypatch.setenv(name, value)
    with WaveSpinner("working", delay=0, interval=0, stream=stream):
        time.sleep(0.01)
    assert stream.getvalue() == ""


def test_fast_work_never_draws() -> None:
    stream = Terminal()
    with WaveSpinner("working", delay=0.05, interval=0.01, stream=stream):
        time.sleep(0.01)
    assert stream.getvalue() == ""


def test_spinner_preserves_stdout_and_clears_after_success() -> None:
    import sys

    stream = Terminal()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "stdout", io.StringIO())
        patch.setattr(sys, "stderr", stream)
        sys.stdout.write('{"ready":true}')
        with WaveSpinner("checking readiness", delay=0, interval=0.005):
            time.sleep(0.02)
        assert sys.stdout.getvalue() == '{"ready":true}'
    assert "\033[2K" in stream.getvalue()
    assert stream.getvalue().endswith("\r\033[2K")


def test_line_cleared_after_exception() -> None:
    stream = Terminal()
    with pytest.raises(RuntimeError, match="failed"):
        with WaveSpinner("working", delay=0, interval=0.005, stream=stream):
            time.sleep(0.02)
            raise RuntimeError("failed")
    assert stream.getvalue().endswith("\r\033[2K")


def test_non_utf8_terminal_uses_ascii_frame() -> None:
    stream = Terminal()
    stream.encoding = "ascii"
    with WaveSpinner("working", delay=0, interval=0.005, stream=stream):
        time.sleep(0.01)
    assert "[-]" in stream.getvalue()


def test_wave_is_cleared_before_first_prompt_or_output(monkeypatch) -> None:
    import sys

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("SNODO_NO_SPINNER", raising=False)
    monkeypatch.delenv("SNODO_MCP_SERVER", raising=False)
    monkeypatch.delenv("SNODO_JOB_ID", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    stdout = io.StringIO()
    stderr = Terminal()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    with wave_while_silent("checking readiness"):
        time.sleep(0.35)
        sys.stdout.write("Question? ")
        sys.stdout.flush()
        assert stderr.getvalue().endswith("\r\033[2K")
        sys.stdout.write("answer\n")
    assert stdout.getvalue() == "Question? answer\n"
    assert stderr.getvalue().count("\r\033[2K") >= 2


def test_wave_while_silent_never_draws_when_stderr_is_not_tty(monkeypatch) -> None:
    import sys

    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    with wave_while_silent("testing providers"):
        time.sleep(0.02)
        sys.stdout.write("provider output\n")
    assert stderr.getvalue() == ""


def _enable_wave(monkeypatch) -> None:
    for name in (
        "CI",
        "NO_COLOR",
        "SNODO_NO_SPINNER",
        "SNODO_MCP_SERVER",
        "SNODO_JOB_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TERM", "xterm")


def test_rich_console_output_stops_and_clears_wave(monkeypatch) -> None:
    import sys

    _enable_wave(monkeypatch)
    stdout, stderr = Terminal(), Terminal()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    with wave_while_silent("loading tasks"):
        time.sleep(0.35)
        console = Console(file=sys.stdout, force_terminal=False)
        console.print(Table("task"))
        cleared = stderr.getvalue()
        time.sleep(0.04)
        assert stderr.getvalue() == cleared
        assert cleared.endswith("\r\033[2K")


def test_preexisting_rich_console_output_stops_wave(monkeypatch) -> None:
    import sys

    _enable_wave(monkeypatch)
    stdout, stderr = Terminal(), Terminal()
    console = Console(file=stdout, force_terminal=False)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    with wave_while_silent("loading tasks"):
        time.sleep(0.35)
        console.print("table from old console")
        cleared = stderr.getvalue()
        time.sleep(0.04)
        assert stderr.getvalue() == cleared
        assert cleared.endswith("\r\033[2K")
    assert "table from old console" in stdout.getvalue()


def test_rich_pager_entry_stops_wave_before_pager_starts(monkeypatch) -> None:
    import sys

    _enable_wave(monkeypatch)
    stdout, stderr = Terminal(), Terminal()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    pager_started = []
    original_pager = Console.pager

    def observe_pager(console, *args, **kwargs):
        assert stderr.getvalue().endswith("\r\033[2K")
        pager_started.append(True)
        return original_pager(console, *args, **kwargs)

    monkeypatch.setattr(Console, "pager", observe_pager)
    with wave_while_silent("loading tasks"):
        time.sleep(0.35)
        console = Console(file=sys.stdout, force_terminal=False)
        with console.pager():
            console.print("paged task output")
        stopped = stderr.getvalue()
        time.sleep(0.04)
        assert stderr.getvalue() == stopped
    assert pager_started == [True]
    assert "paged task output" in stdout.getvalue()
    assert "loading tasks" not in stdout.getvalue()


def test_builtin_prompt_stops_wave_before_waiting(monkeypatch) -> None:
    import builtins
    import sys

    _enable_wave(monkeypatch)
    stdout, stderr = Terminal(), Terminal()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(builtins, "input", lambda prompt="": prompt)
    with wave_while_silent("waiting"):
        time.sleep(0.35)
        answer = input("Continue? ")
        assert answer == "Continue? "
        assert stderr.getvalue().endswith("\r\033[2K")
        stopped = stderr.getvalue()
        time.sleep(0.04)
        assert stderr.getvalue() == stopped


def test_output_keeps_wave_stopped_during_later_silence(monkeypatch) -> None:
    import sys

    _enable_wave(monkeypatch)
    stdout, stderr = Terminal(), Terminal()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    with wave_while_silent("loading tasks"):
        time.sleep(0.35)
        sys.stdout.write("first output\n")
        after_output = stderr.getvalue()
        time.sleep(0.35)
        assert stderr.getvalue() == after_output
    assert stdout.getvalue() == "first output\n"


def test_rich_output_preserves_stdout_bytes(monkeypatch) -> None:
    import sys

    _enable_wave(monkeypatch)
    stdout, stderr = Terminal(), Terminal()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    table = Table("task")
    table.add_row("unchanged")
    expected = io.StringIO()
    Console(file=expected, force_terminal=False, color_system=None).print(table)
    with wave_while_silent("loading tasks"):
        time.sleep(0.35)
        Console(file=sys.stdout, force_terminal=False, color_system=None).print(table)
    assert stdout.getvalue().encode() == expected.getvalue().encode()


def test_wave_while_silent_restores_global_output_state_on_exception(monkeypatch) -> None:
    import sys

    stdout, stderr = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    original_stdout = sys.__stdout__
    original_stderr = sys.__stderr__
    original_print = Console.print

    with pytest.raises(RuntimeError, match="failed"):
        with wave_while_silent("loading tasks"):
            assert sys.stdout is not stdout
            assert sys.stderr is not stderr
            assert Console.print is not original_print
            raise RuntimeError("failed")

    assert sys.stdout is stdout
    assert sys.stderr is stderr
    assert sys.__stdout__ is original_stdout
    assert sys.__stderr__ is original_stderr
    assert Console.print is original_print


def test_wave_output_completes_under_a_real_pty(monkeypatch) -> None:
    """A real terminal must not deadlock while output stops the animation."""
    for name in (
        "CI",
        "NO_COLOR",
        "SNODO_NO_SPINNER",
        "SNODO_MCP_SERVER",
        "SNODO_JOB_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TERM", "xterm")
    master, slave = pty.openpty()
    code = "\n".join(
        (
            "import time",
            "from snodo.cli.spinner import wave_while_silent",
            "with wave_while_silent('loading tasks'):",
            "    time.sleep(.35)",
            "    print('task table')",
        )
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.DEVNULL,
        stdout=slave,
        stderr=slave,
        close_fds=True,
        env=os.environ.copy(),
    )
    os.close(slave)
    output = bytearray()
    deadline = time.monotonic() + 4
    try:
        while process.poll() is None and time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    output.extend(os.read(master, 4096))
                except OSError:
                    break
        assert process.poll() is not None, "CLI remained blocked under a PTY"
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            output.extend(chunk)
        assert process.returncode == 0
        assert b"task table" in output
        assert b"\x1b[2K" in output
        assert output.rstrip().endswith(b"task table\r") or output.rstrip().endswith(
            b"task table"
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
