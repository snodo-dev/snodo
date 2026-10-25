"""Exercise streamable HTTP serving and Host protection in a real process."""

import http.client
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _free_loopback_port() -> int:
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return listener.getsockname()[1]
    except OSError as exc:
        pytest.skip(f"sandbox cannot bind a loopback socket: {exc}")


def _request(port: int, host: str) -> int:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", "/mcp", headers={"Host": host})
        return connection.getresponse().status
    finally:
        connection.close()


def test_real_streamable_http_server_refuses_foreign_hosts_and_stops(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / ".snodo").mkdir()
    (repo / ".snodo" / "protocol.yml").write_text(
        "protocol_id: test\n"
        "name: Test\n"
        "version: 1.0.0\n"
        "modes:\n"
        "  - mode_id: producer\n"
        "    name: Producer\n"
        "    tools: []\n"
        "    validators: []\n"
        "validators:\n"
        "  - validator_id: security\n"
        "    validator_type: security\n"
        "    criteria: [Check security]\n"
        "disagreement_policy: unanimous\n"
        "initial_mode: producer\n"
    )
    port = _free_loopback_port()
    proc = subprocess.Popen(  # noqa: S603 - controlled test argv
        [sys.executable, "-m", "snodo", "serve", "--transport", "streamable-http",
         "--protocol", ".snodo/protocol.yml", "--port", str(port)],
        cwd=repo,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([str(Path.cwd()), *[p for p in sys.path if p]])},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"serve process exited during startup with {proc.returncode}: {proc.stderr.read().decode(errors='replace')}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            pytest.fail("serve process did not accept loopback connections")

        good_status = _request(port, f"127.0.0.1:{port}")
        assert good_status != 421, f"good Host was refused with HTTP {good_status}"
        foreign_status = _request(port, "foreign.example")
        assert foreign_status == 421, f"foreign Host was not refused: HTTP {foreign_status}"
        assert foreign_status != good_status

        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        # uvicorn finishes its graceful shutdown and then re-raises SIGTERM, so a
        # clean stop is either exit 0 or death by that signal; a hang or a
        # different signal is the failure.
        assert proc.wait(timeout=10) in (0, -signal.SIGTERM)
    finally:
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            proc.wait(timeout=5)
