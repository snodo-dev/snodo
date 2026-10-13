"""Reusable plain-text and coloured Snodo CLI wordmark."""

from __future__ import annotations

import os
import sys
from typing import TextIO

from snodo.version import __version__

_ROWS = (
    "╭──  ┌─╮  ╭─╮  ╭─┤  ╭─╮   ╭─┤  ╭─╮  ╷   ╷",
    "╰─╮  │ │  │ │  │ │  │ │   │ │  ├─╯  ╰╮ ╭╯",
)
_LAST_ROW_PREFIX = "──╯  ╵ ╵  ╰─╯  ╰─╯  ╰─╯"
_LAST_ROW_SUFFIX = "╰─╯  ╰──   ╰─╯"
_GREY = "\033[38;2;220;227;230m"
_TEAL = "\033[38;2;44;207;188m"
_SLATE = "\033[38;2;100;127;143m"
_RESET = "\033[0m"


def plain_wordmark() -> str:
    """Return the three-row wordmark and current version without ANSI codes."""
    return "\n".join((*_ROWS, f"{_LAST_ROW_PREFIX} ▪ {_LAST_ROW_SUFFIX}  {__version__}"))


def colored_wordmark() -> str:
    """Return the wordmark with its approved 24-bit ANSI colours."""
    if "NO_COLOR" in os.environ:
        return plain_wordmark()
    first_rows = "\n".join(f"{_GREY}{row}{_RESET}" for row in _ROWS)
    last_row = (
        f"{_GREY}{_LAST_ROW_PREFIX}{_RESET} "
        f"{_TEAL}▪ {_LAST_ROW_SUFFIX}{_RESET}  "
        f"{_SLATE}{__version__}{_RESET}"
    )
    return f"{first_rows}\n{last_row}"


def should_show_wordmark(stream: TextIO | None = None) -> bool:
    """Return whether the wordmark should be shown for a TTY stream."""
    target = stream if stream is not None else sys.stdout
    try:
        return target.isatty()
    except (AttributeError, OSError):
        return False
