"""Focused tests for the managed-tunnel subprocess helpers."""

import collections
import errno
import io
import signal
import subprocess
import threading
from unittest.mock import Mock, call

import pytest

from snodo.cli.commands import serve_tunnel_process as process


def test_terminate_reaps_after_term_and_does_not_escalate():
    child = Mock(pid=123, wait=Mock(return_value=0))
    with pytest.MonkeyPatch.context() as mp:
        signal_group = Mock()
        mp.setattr(process, "signal_process_group", signal_group)
        process.terminate_process_group(child, timeout=0.25)
    signal_group.assert_called_once_with(123, signal.SIGTERM)
    child.wait.assert_called_once_with(timeout=0.25)


def test_terminate_escalates_after_timeout_and_reaps():
    child = Mock(pid=123, wait=Mock(side_effect=[subprocess.TimeoutExpired("x", 1), 0]))
    with pytest.MonkeyPatch.context() as mp:
        signal_group = Mock()
        mp.setattr(process, "signal_process_group", signal_group)
        process.terminate_process_group(child)
    assert signal_group.call_args_list == [call(123, signal.SIGTERM), call(123, signal.SIGKILL)]
    assert child.wait.call_args_list == [call(timeout=5.0), call(timeout=2)]


def test_terminate_handles_none_and_second_timeout():
    process.terminate_process_group(None)
    child = Mock(pid=9, wait=Mock(side_effect=subprocess.TimeoutExpired("x", 1)))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(process, "signal_process_group", Mock())
        process.terminate_process_group(child)
    assert child.wait.call_count == 2


def test_signal_falls_back_to_pid_when_group_is_missing_and_swallows_race(monkeypatch):
    calls = []
    monkeypatch.setattr(process.os, "getpgid", Mock(side_effect=ProcessLookupError()))
    monkeypatch.setattr(process.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    process.signal_process_group(42, signal.SIGTERM)
    assert calls == [(42, signal.SIGTERM)]

    monkeypatch.setattr(process.os, "kill", Mock(side_effect=OSError(errno.ESRCH, "gone")))
    process.signal_process_group(42, signal.SIGTERM)


def test_signal_uses_group_then_pid_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(process.os, "getpgid", lambda pid: pid + 1)

    def killpg(group, sig):
        calls.append(("group", group, sig))
        raise PermissionError(errno.EPERM, "denied")

    monkeypatch.setattr(process.os, "killpg", killpg)
    monkeypatch.setattr(process.os, "kill", lambda pid, sig: calls.append(("pid", pid, sig)))
    process.signal_process_group(42, signal.SIGTERM)
    assert calls == [("group", 43, signal.SIGTERM), ("pid", 42, signal.SIGTERM)]


def test_wait_for_bind_checks_survival_and_injects_sleep():
    child = Mock(poll=Mock(side_effect=[None, 7]))
    slept = []
    assert process.wait_for_server_bind(child, slept.append)
    assert not process.wait_for_server_bind(child, slept.append)
    assert slept == [2.0, 2.0]


def test_wait_for_bind_defaults_to_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(process.time, "sleep", slept.append)
    assert process.wait_for_server_bind(Mock(poll=lambda: None))
    assert slept == [2.0]


def _join(thread: threading.Thread) -> None:
    thread.join(timeout=2)
    assert not thread.is_alive(), "pipe reader failed to finish"


def test_drain_stream_retains_lines_calls_callback_and_closes():
    stream = io.StringIO("first\nsecond\n")
    sink = collections.deque(maxlen=1)
    seen = []
    thread = process.drain_stream(stream, sink, seen.append)
    assert thread is not None
    _join(thread)
    assert list(sink) == ["second\n"]
    assert seen == ["first\n", "second\n"]
    assert stream.closed


@pytest.mark.parametrize("stream", [None, Mock()])
def test_drain_stream_ignores_absent_or_non_io_stream(stream):
    assert process.drain_stream(stream) is None


class _ReadFailure(io.IOBase):
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)

    def readline(self, *args):
        outcome = next(self.outcomes, "")
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.mark.parametrize("failure", [ValueError("closed"), OSError("read failed")])
def test_drain_stream_stops_on_read_errors_and_closes(failure):
    stream = _ReadFailure([failure])
    thread = process.drain_stream(stream)
    _join(thread)
    assert stream.closed


def test_drain_stream_treats_decode_error_as_value_error_and_closes():
    # UnicodeDecodeError subclasses ValueError, so the earlier read-error
    # handler wins; preserve the actual contract rather than assuming retry.
    stream = _ReadFailure([UnicodeDecodeError("utf8", b"x", 0, 1, "bad"), "after\n"])
    thread = process.drain_stream(stream)
    _join(thread)
    assert stream.closed


def test_callback_error_does_not_stop_reading():
    stream = io.StringIO("first\nsecond\n")
    seen = []
    sink = collections.deque()

    def callback(line):
        seen.append(line)
        raise RuntimeError("observer failed")

    sink = collections.deque()
    thread = process.drain_stream(stream, sink, callback)
    _join(thread)
    assert list(sink) == ["first\n", "second\n"]
    assert seen == ["first\n", "second\n"]
    assert stream.closed


class _CloseFailure(io.IOBase):
    emitted = False

    def readline(self, *args):
        if self.emitted:
            return ""
        self.emitted = True
        return "line\n"

    def close(self):
        raise OSError("close race")

    def __del__(self):
        pass


def test_drain_stream_guards_close_failure():
    thread = process.drain_stream(_CloseFailure())
    _join(thread)


def test_captured_stderr_prefers_tail_then_reads_fallback():
    assert process.captured_stderr(Mock(stderr=Mock()), collections.deque(["tail\n"])) == "tail\n"
    stream = Mock(read=Mock(return_value="fallback"))
    assert process.captured_stderr(Mock(stderr=stream), collections.deque()) == "fallback"
    stream.read.assert_called_once_with()


@pytest.mark.parametrize("failure", [ValueError("closed"), OSError("read failed")])
def test_captured_stderr_guards_fallback_read_errors(failure):
    stream = Mock(read=Mock(side_effect=failure))
    assert process.captured_stderr(Mock(stderr=stream), collections.deque()) == ""


def test_captured_stderr_without_stderr_returns_empty():
    assert process.captured_stderr(Mock(stderr=None), collections.deque()) == ""
