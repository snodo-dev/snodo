"""A stopped server leaves no child holding its port; a bind that cannot happen
explains why (Fixes #290).

FILE: tests/mcp/test_serve_lifecycle.py

Two faults share one root: a server that spawns children must own their
lifetime. ``_run_tunnel`` terminated only the direct MCP child, so anything it
had started — the listener holding port 55441 — survived the server. The next
start then failed with a raw ``[Errno 48] ... address already in use``.

These tests drive the real helpers with a real process group, so a regression
that stops reaping the group fails here rather than on an operator's machine.
"""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from snodo.cli.commands import serve_cmd

_LISTEN_SCRIPT = (
    "import os, socket, time\n"
    "s = socket.socket()\n"
    "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
    "s.bind(('127.0.0.1', int(os.environ['SNODO_TEST_PORT'])))\n"
    "s.listen(1)\n"
    "open(os.environ['SNODO_TEST_MARKER'], 'w').write('ready')\n"
    "time.sleep(300)\n"
)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _spawn_listener(port: int, marker: Path) -> subprocess.Popen:
    """Start a listener in its own session, shaped like the MCP child."""
    return subprocess.Popen(  # noqa: S603 - test-controlled argv list, no shell
        [sys.executable, "-c", _LISTEN_SCRIPT],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
        env={
            **os.environ,
            "SNODO_TEST_PORT": str(port),
            "SNODO_TEST_MARKER": str(marker),
        },
    )


def _wait_for_ready(marker: Path, proc: subprocess.Popen, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker.exists():
            return
        if proc.poll() is not None:
            raise AssertionError("holder died before it listened")
        time.sleep(0.05)
    raise AssertionError("holder never became ready")


def _reap(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def test_terminate_process_group_reaps_a_grandchild_holding_the_port(tmp_path):
    """Stopping the server frees its port: no descendant outlives it.

    The MCP child starts a listener (here a grandchild in the same session).
    Terminating only the direct child leaves the port held; signalling the whole
    process group is what actually stops it (Fixes #290).
    """
    port = _free_port()
    marker = tmp_path / "ready"
    holder = _spawn_listener(port, marker)
    try:
        _wait_for_ready(marker, holder)
        assert serve_cmd._port_holder_pid(port) is not None

        serve_cmd._terminate_process_group(holder)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and serve_cmd._port_holder_pid(port) is not None:
            time.sleep(0.05)
        assert serve_cmd._port_holder_pid(port) is None
    finally:
        _reap(holder)


def test_terminate_process_group_kills_a_child_holding_a_grandchild(tmp_path):
    """The direct child is in the group too, so a grandchild listener goes with it."""
    port = _free_port()
    marker = tmp_path / "ready"
    # The outer process starts the listener as a child, then sleeps: the MCP
    # child/grandchild shape the tunnel path actually produces.
    outer = subprocess.Popen(  # noqa: S603 - test-controlled argv list, no shell
        [
            sys.executable,
            "-c",
            (
                "import os, subprocess, sys, time\n"
                "subprocess.Popen([sys.executable, '-c', os.environ['SNODO_TEST_INNER']],\n"
                "                 env=os.environ, stdout=subprocess.DEVNULL,\n"
                "                 stderr=subprocess.DEVNULL)\n"
                "time.sleep(300)\n"
            ),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
        env={
            **os.environ,
            "SNODO_TEST_PORT": str(port),
            "SNODO_TEST_MARKER": str(marker),
            "SNODO_TEST_INNER": _LISTEN_SCRIPT,
        },
    )
    try:
        _wait_for_ready(marker, outer)
        assert serve_cmd._port_holder_pid(port) is not None

        serve_cmd._terminate_process_group(outer)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and serve_cmd._port_holder_pid(port) is not None:
            time.sleep(0.05)
        assert serve_cmd._port_holder_pid(port) is None
    finally:
        _reap(outer)


def test_port_in_use_explanation_without_a_holder_names_the_lookup():
    """With no identifiable holder the message still avoids a raw traceback."""
    explanation = serve_cmd._port_in_use_explanation(_free_port())

    assert "already in use" in explanation
    assert "lsof" in explanation
    assert "[Errno 48]" not in explanation


def test_port_in_use_explanation_reports_the_holder(tmp_path):
    """The explanation names the process and how long it has held the port."""
    port = _free_port()
    marker = tmp_path / "ready"
    holder = _spawn_listener(port, marker)
    try:
        _wait_for_ready(marker, holder)
        explanation = serve_cmd._port_in_use_explanation(port)

        assert f"Port {port} is already in use" in explanation
        assert str(holder.pid) in explanation
        assert "held it for" in explanation
        assert "[Errno 48]" not in explanation
    finally:
        _reap(holder)


def test_run_server_refuses_when_port_held(capsys):
    """_run_server reports the holder instead of starting uvicorn into Errno 48."""
    mock_protocol = MagicMock()
    mock_protocol.protocol_id = "test"
    mock_protocol.modes = []
    mock_protocol.get_mode.return_value = None

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml", mode=None, transport="sse", port=55441,
    )

    with patch("snodo.mcp.server.ProtocolMCPServer"):
        with patch("snodo.mcp.transport.build_fastmcp_server") as mock_build:
            mock_mcp = MagicMock()
            mock_mcp.settings.port = 8000
            mock_build.return_value = mock_mcp
            with patch(
                "snodo.cli.commands.serve_cmd._port_holder_pid", return_value=4321,
            ):
                with patch(
                    "snodo.cli.commands.serve_cmd._describe_process",
                    return_value="pid 4321 (opencode mcp) has held it for 00:42:10",
                ):
                    result = serve_cmd._run_server(args, mock_protocol)

    assert result == 1
    assert mock_mcp.run.call_count == 0
    err = capsys.readouterr().err
    assert "already in use" in err
    assert "4321" in err
    assert "00:42:10" in err
    assert "[Errno 48]" not in err


def test_run_server_preflight_ignores_stdio():
    """stdio owns no port, so the preflight never refuses it."""
    mock_protocol = MagicMock()
    mock_protocol.protocol_id = "test"
    mock_protocol.modes = []
    mock_protocol.get_mode.return_value = None

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml", mode=None, transport="stdio", port=55441,
    )

    with patch("snodo.mcp.server.ProtocolMCPServer"):
        with patch("snodo.mcp.transport.build_fastmcp_server") as mock_build:
            mock_mcp = MagicMock()
            mock_mcp.settings.port = 8000
            mock_build.return_value = mock_mcp
            with patch(
                "snodo.cli.commands.serve_cmd._port_holder_pid",
                side_effect=AssertionError("stdio must not probe a port"),
            ):
                assert serve_cmd._run_server(args, mock_protocol) == 0
