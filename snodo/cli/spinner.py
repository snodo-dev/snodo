"""Delayed, stderr-only Snodo wave indicator for slow CLI operations."""

from __future__ import annotations

import os
import sys
import threading
import time
from contextlib import contextmanager
from types import TracebackType
from typing import Iterator, TextIO

_CYCLE = "▁▂▃▄▅▆▇█▇▆▅▄▃▂"
_ASCII_FRAMES = ("[-]", "[\\]", "[|]", "[/]")


def wave_frame(index: int) -> str:
    """Return one five-cell wave frame using the agreed staggered cycle."""
    size = len(_CYCLE)
    return "[" + "".join(_CYCLE[(index + offset) % size] for offset in (0, 2, 4, 6, 8)) + "]"


class WaveSpinner:
    """Context manager that briefly delays an animated status on stderr."""

    def __init__(
        self,
        label: str,
        *,
        delay: float = 0.3,
        interval: float = 0.11,
        stream: TextIO | None = None,
    ) -> None:
        self.label = label
        self.delay = delay
        self.interval = interval
        self.stream = stream
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at = 0.0
        self._drawn = False
        self._closed = False

    def _enabled(self) -> bool:
        stream = self.stream if self.stream is not None else sys.stderr
        self.stream = stream
        try:
            if not stream.isatty():
                return False
        except (AttributeError, OSError):
            return False
        return not (
            os.environ.get("CI")
            or "NO_COLOR" in os.environ
            or os.environ.get("TERM") == "dumb"
            or os.environ.get("SNODO_NO_SPINNER") == "1"
            or os.environ.get("SNODO_MCP_SERVER")
            or os.environ.get("SNODO_JOB_ID")
        )

    def __enter__(self) -> WaveSpinner:
        if self._enabled():
            self._started_at = time.monotonic()
            self._thread = threading.Thread(target=self._animate, daemon=True)
            self._thread.start()
        return self

    def _animate(self) -> None:
        assert self.stream is not None
        if self._stop.wait(self.delay):
            return
        self._drawn = True
        index = 0
        while not self._stop.is_set():
            elapsed = time.monotonic() - self._started_at
            frame = wave_frame(index)
            try:
                if "utf" not in (getattr(self.stream, "encoding", "") or "").lower():
                    frame = _ASCII_FRAMES[index % len(_ASCII_FRAMES)]
                self.stream.write(f"\r\033[2K\033[36m{frame}\033[0m \033[2m{self.label}… {elapsed:.1f}s\033[0m")
                self.stream.flush()
            except (OSError, UnicodeError):
                return
            index += 1
            self._stop.wait(self.interval)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._closed:
            return
        self._closed = True
        if self._thread is not None:
            self._stop.set()
            self._thread.join()
            if self._drawn and self.stream is not None:
                try:
                    self.stream.write("\r\033[2K")
                    self.stream.flush()
                except (OSError, UnicodeError):
                    pass


class _OutputBoundary:
    """Stop the spinner before forwarding a command's first real output."""

    def __init__(self, stream: TextIO, spinner: WaveSpinner) -> None:
        self._stream = stream
        self._spinner = spinner

    def write(self, value: str) -> int:
        if value:
            self._spinner.__exit__(None, None, None)
        return self._stream.write(value)

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


@contextmanager
def wave_while_silent(label: str) -> Iterator[None]:
    """Show a delayed wave only until the CLI is ready to print anything.

    Wrapping both output streams, rather than clearing only when a callback
    returns, keeps long commands responsive while guaranteeing the animation
    cannot overwrite JSON, progress output, or an interactive prompt.
    """
    spinner = WaveSpinner(label)
    stdout, stderr = sys.stdout, sys.stderr
    spinner.__enter__()
    sys.stdout = _OutputBoundary(stdout, spinner)  # type: ignore[assignment]
    sys.stderr = _OutputBoundary(stderr, spinner)  # type: ignore[assignment]
    try:
        yield
    finally:
        sys.stdout, sys.stderr = stdout, stderr
        spinner.__exit__(None, None, None)
