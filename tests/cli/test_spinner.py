"""Contract tests for the reusable CLI wave indicator (Fixes #780)."""

import io
import time

import pytest

from snodo.cli.spinner import WaveSpinner, wave_frame


class Terminal(io.StringIO):
    """In-memory terminal stream with UTF-8 encoding."""

    encoding = "utf-8"

    def isatty(self) -> bool:
        return True


def test_wave_frame_follows_staggered_cycle() -> None:
    cycle = "▁▂▃▄▅▆▇█▇▆▅▄▃▂"
    for index in range(len(cycle)):
        assert wave_frame(index) == "[" + "".join(
            cycle[(index + offset) % len(cycle)] for offset in (0, 2, 4, 6, 8)
        ) + "]"


def test_non_tty_writes_to_neither_stream(capsys) -> None:
    with WaveSpinner("working", delay=0, interval=0):
        time.sleep(0.01)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("name,value", [
    ("CI", "true"), ("NO_COLOR", ""), ("TERM", "dumb"), ("SNODO_NO_SPINNER", "1"),
])
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
