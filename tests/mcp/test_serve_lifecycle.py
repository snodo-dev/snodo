"""A stopped server leaves no child holding its port; a bind that cannot happen
explains why; a server with no --port finds its own (Fixes #290, #309).

FILE: tests/mcp/test_serve_lifecycle.py

Two faults share one root: a server that spawns children must own their
lifetime. ``_run_tunnel`` terminated only the direct MCP child, so anything it
had started — the listener holding port 55441 — survived the server. The next
start then failed with a raw ``[Errno 48] ... address already in use``.

The other root: ``snodo serve`` defaulted to one fixed port for every server on
a machine, so a second server for the same project — in another mode, or for
another project — refused to start. With no ``--port`` a server now finds a
free one and says which; an explicit ``--port`` still means that port and fails
loudly, holder named, when it is taken.

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

import pytest

from snodo.cli.commands import serve_cmd


@pytest.fixture(autouse=True)
def _isolate_environ(monkeypatch):
    """_run_server writes FORWARDED_ALLOW_IPS into os.environ and leaves it set;
    swap in a throwaway copy so the write cannot leak into later tests (#200)."""
    monkeypatch.setattr(os, "environ", os.environ.copy())


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


# === A server with no --port finds its own (Fixes #309) ===


def _run_server_with_held(held, *, mode=None):
    """Run _run_server against a fake holder set; return (result, port, err).

    ``held`` is the set of ports some other server owns. The port-holder probe
    answers from it, and the run reports which port it chose on stderr.
    """
    import io
    from contextlib import redirect_stderr

    mock_protocol = MagicMock()
    mock_protocol.protocol_id = "test"
    mock_protocol.modes = []
    mock_protocol.get_mode.return_value = MagicMock()  # any named mode is valid

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml", mode=mode, transport="sse", port=None,
    )

    err = io.StringIO()
    with patch("snodo.mcp.server.ProtocolMCPServer"):
        with patch("snodo.mcp.transport.build_fastmcp_server") as mock_build:
            mock_mcp = MagicMock()
            mock_mcp.settings.port = 8000
            mock_build.return_value = mock_mcp
            with patch(
                "snodo.cli.commands.serve_cmd._port_holder_pid",
                side_effect=lambda port, host="127.0.0.1": 1 if port in held else None,
            ):
                with redirect_stderr(err):
                    result = serve_cmd._run_server(args, mock_protocol)
    return result, mock_mcp.settings.port, err.getvalue()


def test_two_servers_without_a_port_both_start_on_different_ports():
    """The second no---port server starts beside the first, on its own port."""
    held: set[int] = set()

    first_result, first_port, first_err = _run_server_with_held(held)
    assert first_result == 0
    assert f"using free port {first_port}" in first_err
    held.add(first_port)  # the first server now owns its port

    second_result, second_port, second_err = _run_server_with_held(held)
    assert second_result == 0
    assert f"using free port {second_port}" in second_err
    assert first_port != second_port


def test_two_modes_of_one_project_both_start():
    """Mode is not part of the port: two modes of the same project coexist."""
    held: set[int] = set()

    producer_result, producer_port, _ = _run_server_with_held(held, mode="producer")
    assert producer_result == 0
    held.add(producer_port)

    reviewer_result, reviewer_port, _ = _run_server_with_held(held, mode="reviewer")
    assert reviewer_result == 0
    assert producer_port != reviewer_port


def test_choosing_a_port_does_not_bind_it():
    """The chosen port is not held by this process — the check never binds.

    Binding a port to test it and releasing it before the real bind would make
    the check briefly the very holder it exists to report. After choosing, no
    listener of ours may exist on the port (Fixes #309).
    """
    chosen = serve_cmd._choose_serve_port(None)

    assert chosen is not None
    assert serve_cmd._port_holder_pid(chosen) is None


def test_explicit_port_in_use_names_the_real_holder(tmp_path, capsys):
    """A named port that is taken still refuses, with the holder named (#290)."""
    port = _free_port()
    marker = tmp_path / "ready"
    holder = _spawn_listener(port, marker)
    try:
        _wait_for_ready(marker, holder)

        mock_protocol = MagicMock()
        mock_protocol.protocol_id = "test"
        mock_protocol.modes = []
        mock_protocol.get_mode.return_value = None
        args = SimpleNamespace(
            protocol=".snodo/protocol.yml", mode=None, transport="sse", port=port,
        )

        with patch("snodo.mcp.server.ProtocolMCPServer"):
            with patch("snodo.mcp.transport.build_fastmcp_server") as mock_build:
                mock_mcp = MagicMock()
                mock_build.return_value = mock_mcp
                result = serve_cmd._run_server(args, mock_protocol)

        err = capsys.readouterr().err
    finally:
        _reap(holder)

    assert result == 1
    assert mock_mcp.run.call_count == 0
    assert f"Port {port} is already in use" in err
    assert str(holder.pid) in err
    assert "held it for" in err
    assert "[Errno 48]" not in err


def test_explicit_port_that_is_free_is_used_unchanged(capsys):
    """A named port is a promise: it is used as given, not scanned away."""
    port = _free_port()

    result, chosen, err = _run_server_explicit(port)

    assert result == 0
    assert chosen == port
    assert "using free port" not in err


def _run_server_explicit(port):
    """Run _run_server with an explicit port; return (result, port, err)."""
    import io
    from contextlib import redirect_stderr

    mock_protocol = MagicMock()
    mock_protocol.protocol_id = "test"
    mock_protocol.modes = []
    mock_protocol.get_mode.return_value = None

    args = SimpleNamespace(
        protocol=".snodo/protocol.yml", mode=None, transport="sse", port=port,
    )

    err = io.StringIO()
    with patch("snodo.mcp.server.ProtocolMCPServer"):
        with patch("snodo.mcp.transport.build_fastmcp_server") as mock_build:
            mock_mcp = MagicMock()
            mock_mcp.settings.port = 8000
            mock_build.return_value = mock_mcp
            with redirect_stderr(err):
                result = serve_cmd._run_server(args, mock_protocol)
    return result, mock_mcp.settings.port, err.getvalue()


# === A failed tunnel start leaves nothing behind and announces nothing (#309) ===


def _run_tunnel_failed_bind(tmp_path, *, tunnel_config=None, port=9090):
    """Drive _run_tunnel to a bind failure; return (result, out, err, deprovisioned).

    The MCP child exits immediately, so ``_wait_for_server_bind`` reports the
    failure. The tunnel is provisioned fresh. Whatever was printed is captured,
    and deprovision calls are recorded.
    """
    import io
    from contextlib import redirect_stdout, redirect_stderr

    mock_protocol = MagicMock()
    mock_protocol.protocol_id = "test"
    args = SimpleNamespace(
        protocol=".snodo/protocol.yml", mode=None,
        transport="streamable-http", port=port, rotate=False, delete=False,
    )

    provisioned = {
        "hostname": "proj-all-abc123.tunnel.snodo.dev",
        "tunnel_token": "tok_xxx",
    }

    bound = MagicMock()
    bound.pid = 12345
    bound.poll.return_value = 1  # exited: the bind failed
    bound.returncode = 3
    bound.stderr.read.return_value = ""

    deprovisioned = []

    out, err = io.StringIO(), io.StringIO()
    with patch("snodo.cli.commands.serve_cmd._check_cloudflared", return_value=True):
        with patch("snodo.cli.commands.serve_cmd._get_snodo_api_key", return_value="key123"):
            with patch("snodo.cli.commands.serve_cmd._load_tunnel_config",
                       return_value=tunnel_config or {}):
                with patch("snodo.cli.commands.serve_cmd._provision_tunnel",
                           return_value=provisioned):
                    with patch("snodo.cli.commands.serve_cmd._save_tunnel_config"):
                        with patch("snodo.cli.commands.serve_cmd._delete_tunnel_file"):
                            with patch("snodo.cli.commands.serve_cmd._deprovision_tunnel",
                                       side_effect=lambda api, host: deprovisioned.append(host) or True):
                                with patch("snodo.cli.commands.serve_cmd._port_holder_pid",
                                           return_value=None):
                                    with patch("snodo.cli.commands.serve_cmd.subprocess.Popen",
                                               return_value=bound):
                                        with patch("snodo.cli.commands.serve_cmd._terminate_process_group"):
                                            with redirect_stdout(out), redirect_stderr(err):
                                                result = serve_cmd._run_tunnel(
                                                    args, mock_protocol, ".snodo/protocol.yml")

    return result, out.getvalue(), err.getvalue(), deprovisioned


def test_failed_bind_prints_no_active_url(tmp_path):
    """Nothing is called active before the server is actually listening."""
    result, out, err, _ = _run_tunnel_failed_bind(tmp_path)

    assert result == 1
    assert "tunnel active" not in out
    assert "tunnel active" not in err
    assert "tunnel.snodo.dev/mcp" not in out


def test_failed_bind_rolls_back_a_newly_provisioned_tunnel(tmp_path):
    """A start that fails does not leave the tunnel it just made behind."""
    result, _out, _err, deprovisioned = _run_tunnel_failed_bind(tmp_path)

    assert result == 1
    assert deprovisioned == ["proj-all-abc123.tunnel.snodo.dev"]


def test_failed_bind_does_not_remove_an_existing_tunnel(tmp_path):
    """Only a tunnel this run created is rolled back; an existing one stays."""
    stored = {"hostname": "existing.tunnel.snodo.dev", "tunnel_token": "tok_old"}

    result, _out, _err, deprovisioned = _run_tunnel_failed_bind(
        tmp_path, tunnel_config=stored)

    assert result == 1
    assert deprovisioned == []


def test_tunnel_targets_the_port_it_chose(tmp_path):
    """The tunnel is provisioned for, and the child told, the chosen port."""
    import io
    from contextlib import redirect_stderr

    mock_protocol = MagicMock()
    args = SimpleNamespace(
        protocol=".snodo/protocol.yml", mode=None,
        transport="streamable-http", port=None, rotate=False, delete=False,
    )
    provisioned = {"hostname": "proj-all-abc123.tunnel.snodo.dev", "tunnel_token": "tok"}

    cf = MagicMock()
    cf.pid = 999
    cf.poll.return_value = None
    cf.stderr.readline.return_value = "Registered tunnel connection\n"
    # Simulate the operator hitting Ctrl+C while the wait loop is blocked in
    # cf_process.wait(); this is the success exit (result 0), distinct from
    # cloudflared exiting on its own (see test_serve_tunnel_cloudflared_exit.py).
    # The second call is the one _terminate_process_group makes during cleanup.
    cf.wait.side_effect = [KeyboardInterrupt(), 0]

    captured = {}

    def fake_popen(cmd, **kwargs):
        if cmd[0] == "cloudflared":
            return cf
        captured["mcp_cmd"] = cmd
        mcp = MagicMock()
        mcp.pid = 12345
        mcp.poll.return_value = None
        return mcp

    def fake_provision(api_key, slug, mode, short_id, version, port):
        captured["provision_port"] = port
        return provisioned

    err = io.StringIO()
    with patch("snodo.cli.commands.serve_cmd._check_cloudflared", return_value=True):
        with patch("snodo.cli.commands.serve_cmd._get_snodo_api_key", return_value="key123"):
            with patch("snodo.cli.commands.serve_cmd._load_tunnel_config", return_value={}):
                with patch("snodo.cli.commands.serve_cmd._provision_tunnel",
                           side_effect=fake_provision):
                    with patch("snodo.cli.commands.serve_cmd._save_tunnel_config"):
                        with patch("snodo.cli.commands.serve_cmd._port_holder_pid",
                                   return_value=None):
                            with patch("snodo.cli.commands.serve_cmd.subprocess.Popen",
                                       side_effect=fake_popen):
                                with patch("snodo.cli.commands.serve_cmd.signal.signal"):
                                    with patch("snodo.cli.commands.serve_cmd.time.sleep", lambda _: None):
                                        with redirect_stderr(err):
                                            result = serve_cmd._run_tunnel(
                                                args, mock_protocol, ".snodo/protocol.yml")

    assert result == 0
    chosen = captured["provision_port"]
    assert isinstance(chosen, int)
    assert captured["mcp_cmd"][captured["mcp_cmd"].index("--port") + 1] == str(chosen)
    assert f"using free port {chosen}" in err.getvalue()


# === cloudflared exiting on its own must not orphan the MCP child (#290, #334) ===


def test_cloudflared_exit_terminates_the_mcp_child_and_fails(tmp_path):
    """cloudflared dying on its own is not a success: the still-healthy MCP
    child must be stopped, not left holding the port for the next start to
    trip over (Fixes #290, #334)."""
    import io
    from contextlib import redirect_stderr

    mock_protocol = MagicMock()
    args = SimpleNamespace(
        protocol=".snodo/protocol.yml", mode=None,
        transport="streamable-http", port=None, rotate=False, delete=False,
    )
    provisioned = {"hostname": "proj-all-abc123.tunnel.snodo.dev", "tunnel_token": "tok"}

    cf = MagicMock()
    cf.pid = 999
    cf.poll.return_value = None
    cf.stderr.readline.return_value = "Registered tunnel connection\n"
    cf.stderr.read.side_effect = ValueError("I/O operation on closed file")
    cf.wait.return_value = 0  # cloudflared exits on its own, immediately
    cf.returncode = 1

    mcp = MagicMock()
    mcp.pid = 12345
    mcp.poll.return_value = None  # still healthy when cloudflared dies

    def fake_popen(cmd, **kwargs):
        return cf if cmd[0] == "cloudflared" else mcp

    terminated = []

    def fake_drain(_stream, sink=None, on_line=None):
        if sink is not None and on_line is not None:
            sink.append("cloudflared failed: connection refused\n")
            on_line("Registered tunnel connection\n")
        return None

    err = io.StringIO()
    with patch("snodo.cli.commands.serve_cmd._check_cloudflared", return_value=True):
        with patch("snodo.cli.commands.serve_cmd._get_snodo_api_key", return_value="key123"):
            with patch("snodo.cli.commands.serve_cmd._load_tunnel_config", return_value={}):
                with patch("snodo.cli.commands.serve_cmd._provision_tunnel",
                           return_value=provisioned):
                    with patch("snodo.cli.commands.serve_cmd._save_tunnel_config"):
                        with patch("snodo.cli.commands.serve_cmd._port_holder_pid",
                                   return_value=None):
                            with patch("snodo.cli.commands.serve_cmd.subprocess.Popen",
                                       side_effect=fake_popen):
                                 with patch("snodo.cli.commands.serve_cmd.signal.signal"):
                                     with patch("snodo.cli.commands.serve_cmd.time.sleep", lambda _: None):
                                          with patch(
                                              "snodo.cli.commands.serve_cmd._drain_stream",
                                              side_effect=fake_drain,
                                          ):
                                              with patch(
                                                  "snodo.cli.commands.serve_cmd._terminate_process_group",
                                                  side_effect=lambda proc, timeout=5.0: terminated.append(proc),
                                              ):
                                                  with redirect_stderr(err):
                                                      result = serve_cmd._run_tunnel(
                                                          args, mock_protocol, ".snodo/protocol.yml")

    assert result != 0
    assert mcp in terminated, "the MCP child's process group must be terminated"
    assert "cloudflared" in err.getvalue().lower()
    assert "exited" in err.getvalue().lower()
    assert "connection refused" in err.getvalue()


def test_interrupting_a_running_tunnel_is_a_clean_shutdown(tmp_path):
    """A signal-driven stop returns zero without reporting a crash."""
    import io
    from contextlib import redirect_stderr, redirect_stdout

    mock_protocol = MagicMock()
    args = SimpleNamespace(
        protocol=".snodo/protocol.yml", mode=None,
        transport="streamable-http", port=None, rotate=False, delete=False,
    )
    provisioned = {"hostname": "proj-all-abc123.tunnel.snodo.dev", "tunnel_token": "tok"}

    cf = MagicMock()
    cf.pid = 999
    cf.poll.return_value = None
    cf.stderr.readline.return_value = "Registered tunnel connection\n"
    cf.returncode = 0

    mcp = MagicMock()
    mcp.pid = 12345
    mcp.poll.return_value = None

    def fake_popen(cmd, **kwargs):
        return cf if cmd[0] == "cloudflared" else mcp

    handlers = {}
    wait_calls = 0

    def fake_signal(signum, handler):
        handlers[signum] = handler

    def wait_until_interrupted(timeout):
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            handlers[signal.SIGINT](signal.SIGINT, None)
            raise subprocess.TimeoutExpired("cloudflared", timeout)
        return 0

    cf.wait.side_effect = wait_until_interrupted
    terminated = []
    out = io.StringIO()
    err = io.StringIO()
    with patch("snodo.cli.commands.serve_cmd._check_cloudflared", return_value=True):
        with patch("snodo.cli.commands.serve_cmd._get_snodo_api_key", return_value="key123"):
            with patch("snodo.cli.commands.serve_cmd._load_tunnel_config", return_value={}):
                with patch("snodo.cli.commands.serve_cmd._provision_tunnel",
                           return_value=provisioned):
                    with patch("snodo.cli.commands.serve_cmd._save_tunnel_config"):
                        with patch("snodo.cli.commands.serve_cmd._port_holder_pid",
                                   return_value=None):
                            with patch("snodo.cli.commands.serve_cmd.subprocess.Popen",
                                       side_effect=fake_popen):
                                with patch("snodo.cli.commands.serve_cmd.signal.signal",
                                           side_effect=fake_signal):
                                    with patch("snodo.cli.commands.serve_cmd.time.sleep",
                                               lambda _: None):
                                        with patch(
                                            "snodo.cli.commands.serve_cmd._terminate_process_group",
                                            side_effect=lambda proc, timeout=5.0: terminated.append(proc),
                                        ):
                                            with redirect_stdout(out), redirect_stderr(err):
                                                result = serve_cmd._run_tunnel(
                                                    args, mock_protocol, ".snodo/protocol.yml")

    assert result == 0
    assert cf in terminated and mcp in terminated
    assert "Stopping..." in out.getvalue()
    assert "unexpectedly" not in err.getvalue()
    assert "Traceback" not in out.getvalue() + err.getvalue()
