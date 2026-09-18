"""Tests for draining child process pipes in snodo serve --tunnel (Fixes #333).

FILE: tests/cli/test_serve_child_pipes.py
"""

import collections
import subprocess
import sys
import threading

import pytest

from snodo.cli.commands.serve_cmd import _drain_stream


def test_drain_stream_prevents_pipe_buffer_saturation():
    """A child writing far more than any OS pipe buffer does not block when drained."""
    # Write 2MB of lines to stderr: far larger than any OS pipe buffer (typically 16KB-64KB).
    # Without a continuous reader, this subprocess blocks in write() and hangs.
    code = (
        "import sys\n"
        "for i in range(50000):\n"
        "    sys.stderr.write(f'log line {i}\\n')\n"
        "    sys.stderr.flush()\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    sink: collections.deque[str] = collections.deque(maxlen=100)
    drainer = _drain_stream(proc.stderr, sink=sink)
    assert drainer is not None

    # Must finish within 10 seconds. If blocked on pipe buffer, it would time out.
    try:
        ret = proc.wait(timeout=10.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("Child process blocked inside write() — pipe was not drained")

    assert ret == 0
    assert len(sink) == 100
    assert "log line 49999\n" in sink[-1]


def test_drain_stream_preserves_diagnostic_stderr_on_exit():
    """Diagnostic stderr is preserved in the sink deque when the child exits."""
    code = (
        "import sys\n"
        "sys.stderr.write('Error: Address already in use\\n')\n"
        "sys.stderr.write('Traceback: uvicorn failed to bind\\n')\n"
        "sys.exit(3)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    sink: collections.deque[str] = collections.deque(maxlen=1000)
    drainer = _drain_stream(proc.stderr, sink=sink)
    assert drainer is not None

    proc.wait(timeout=5.0)
    drainer.join(timeout=2.0)

    output = "".join(sink)
    assert "Address already in use" in output
    assert "Traceback: uvicorn failed to bind" in output


def test_drain_stream_on_line_callback_detects_connect_line():
    """The on_line callback detects specific trigger lines while draining."""
    code = (
        "import sys, time\n"
        "sys.stderr.write('Starting tunnel client...\\n')\n"
        "sys.stderr.write('Registered tunnel connection connIndex=0\\n')\n"
        "sys.stderr.flush()\n"
        "for i in range(20000):\n"
        "    sys.stderr.write(f'metrics chunk {i}\\n')\n"
        "sys.stderr.flush()\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    connected = threading.Event()

    def on_line(line: str) -> None:
        if "Registered tunnel connection" in line:
            connected.set()

    sink: collections.deque[str] = collections.deque(maxlen=50)
    drainer = _drain_stream(proc.stderr, sink=sink, on_line=on_line)
    assert drainer is not None

    # The callback should fire promptly
    assert connected.wait(timeout=5.0) is True

    # Process should complete without blocking even after emitting tens of thousands of lines
    ret = proc.wait(timeout=10.0)
    assert ret == 0
