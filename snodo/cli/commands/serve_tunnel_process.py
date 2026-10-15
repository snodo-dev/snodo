"""Process-group and pipe helpers for managed tunnel startup."""

import collections
import io
import logging
import os
import signal
import subprocess
import threading
import time
from typing import Any, Callable, Optional

_logger = logging.getLogger(__name__)


def terminate_process_group(proc, timeout: float = 5.0) -> None:
    """Stop a child and its entire session process group, then reap it."""
    if proc is None:
        return
    signal_process_group(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    signal_process_group(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def signal_process_group(pid: int, sig: int) -> None:
    """Signal a process group, falling back to its direct process."""
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, OSError):
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, OSError):
            pass


def wait_for_server_bind(mcp_process, sleep_fn=None) -> bool:
    """Give the local MCP server time to bind and report whether it survived."""
    if sleep_fn is None:
        sleep_fn = time.sleep
    sleep_fn(2.0)
    return mcp_process.poll() is None


def drain_stream(
    stream: Any,
    sink: Optional[collections.deque[str]] = None,
    on_line: Optional[Callable[[str], None]] = None,
) -> Optional[threading.Thread]:
    """Drain a child pipe continuously and retain/callback on each line."""
    if stream is None or not isinstance(stream, io.IOBase):
        return None

    def reader() -> None:
        while True:
            try:
                line = stream.readline()
            except (ValueError, OSError):
                break
            except UnicodeDecodeError:
                continue
            if not line:
                break
            if sink is not None:
                sink.append(line)
            if on_line is not None:
                try:
                    on_line(line)
                except Exception:  # noqa: BLE001 - a log callback cannot stop draining
                    _logger.debug("Tunnel child output callback failed", exc_info=True)
        try:
            stream.close()
        except OSError:
            pass

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    return thread


def captured_stderr(process: Any, tail: collections.deque[str]) -> str:
    """Return drained stderr, with guarded reads for mocked child processes."""
    stderr_output = "".join(tail)
    if not stderr_output and process.stderr:
        try:
            stderr_output = process.stderr.read()
        except (ValueError, OSError):
            stderr_output = ""
    return stderr_output
