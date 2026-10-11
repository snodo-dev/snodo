"""Real-process coverage for stdio serve shutdown on client disconnect."""

import os
import select
import subprocess
import sys
import time
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]


def _start_stdio_server(tmp_path: Path, *, with_worker: bool = False):
    """Start a real CLI server, optionally with a simulated stuck tool worker."""
    snodo_dir = tmp_path / ".snodo"
    snodo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    protocol_source = (
        _ROOT / "packages/snodo-foundation/src/snodo/compiler/protocol.yml"
    )
    (snodo_dir / "protocol.yml").write_text(protocol_source.read_text())

    command = [sys.executable, "-m", "snodo", "serve", "--transport", "stdio"]
    if with_worker:
        # Exercise the same process-lifetime hazard as a stuck synchronous tool
        # call (asyncio.to_thread) while preserving the real stdio transport.
        command = [
            sys.executable,
            "-c",
            (
                "import threading, time; "
                "threading.Thread(target=lambda: time.sleep(300), daemon=True).start(); "
                "from snodo.cli.main import main; "
                "main(['serve', '--transport', 'stdio'])"
            ),
            "serve",
            "--transport",
            "stdio",
        ]
    env = {
        **os.environ,
        "PYTHONPATH": str(_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    return subprocess.Popen(
        command,
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def _assert_exits_after_eof(proc: subprocess.Popen) -> None:
    assert proc.stdin is not None
    proc.stdin.close()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        raise AssertionError("stdio server did not exit after client EOF") from None


def _wait_until_started(proc: subprocess.Popen) -> None:
    """Wait for server startup so EOF is delivered to the transport itself."""
    assert proc.stderr is not None
    deadline = time.monotonic() + 20
    output = ""
    while time.monotonic() < deadline:
        ready, _, _ = select.select([proc.stderr], [], [], 0.1)
        if ready:
            line = proc.stderr.readline()
            output += line
            if "transport=stdio" in line:
                return
        if proc.poll() is not None:
            raise AssertionError(f"stdio server exited during startup: {output}")
    raise AssertionError(f"stdio server did not start: {output}")


def test_real_stdio_server_exits_after_stdin_eof(tmp_path):
    proc = _start_stdio_server(tmp_path)
    try:
        _wait_until_started(proc)
        _assert_exits_after_eof(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_stdio_server_exits_with_active_background_worker(tmp_path):
    proc = _start_stdio_server(tmp_path, with_worker=True)
    try:
        _wait_until_started(proc)
        _assert_exits_after_eof(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
