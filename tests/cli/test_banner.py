"""Tests for the reusable CLI wordmark renderer."""

import re
from io import StringIO

from snodo.cli.banner import colored_wordmark, plain_wordmark, should_show_wordmark
from snodo.version import __version__


def test_plain_wordmark_matches_design_and_current_version() -> None:
    expected = (
        "╭──  ┌─╮  ╭─╮  ╭─┤  ╭─╮   ╭─┤  ╭─╮  ╷   ╷\n"
        "╰─╮  │ │  │ │  │ │  │ │   │ │  ├─╯  ╰╮ ╭╯\n"
        f"──╯  ╵ ╵  ╰─╯  ╰─╯  ╰─╯ ▪ ╰─╯  ╰──   ╰─╯  {__version__}"
    )
    assert plain_wordmark() == expected
    assert all(not row.endswith(" ") for row in plain_wordmark().splitlines())


def test_coloured_wordmark_strips_to_plain_wordmark() -> None:
    coloured = colored_wordmark()
    assert re.sub(r"\x1b\[[0-9;]*m", "", coloured) == plain_wordmark()


def test_no_color_disables_ansi(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    assert "\x1b[" not in colored_wordmark()


def test_show_helper_is_false_for_non_tty_stream() -> None:
    assert not should_show_wordmark(StringIO())
