"""Real-process coverage for subprocess coder descendant cleanup."""

import subprocess
import sys
import time

import psutil
import pytest

from snodo.coders.agy_adapter import AGYAdapter


def _script(tmp_path, child_setup: str, parent_action: str) -> tuple[str, str]:
    pid_file = tmp_path / "children.txt"
    marker = str(tmp_path / "descendant-marker")
    source = (
        "import os, subprocess, sys, time\n"
        f"pid_file = {str(pid_file)!r}\n"
        f"marker = {marker!r}\n"
        f"setup = {child_setup}\n"
        "pids = []\n"
        "for code in setup:\n"
        "    child = subprocess.Popen([sys.executable, '-c', code, marker])\n"
        "    pids.append(str(child.pid))\n"
        "open(pid_file, 'w').write(' '.join(pids))\n"
        "time.sleep(0.15)\n"
        f"{parent_action}\n"
    )
    return source, str(pid_file)


def _wait_dead(pids: list[int], marker: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        live = []
        for pid in pids:
            try:
                process = psutil.Process(pid)
                if process.status() == psutil.STATUS_ZOMBIE:
                    continue
                if marker in process.cmdline():
                    live.append(pid)
            except psutil.NoSuchProcess:
                continue
        if not live:
            return
        time.sleep(0.05)
    assert not live, f"descendants still running: {live}"


def test_silence_halt_kills_plain_and_setsid_grandchildren(tmp_path):
    adapter = AGYAdapter(workspace=tmp_path, timeout_seconds=5, silence_timeout_seconds=2)
    detached = "import os, sys, time; os.setsid(); time.sleep(30)"
    ordinary = "import sys, time; time.sleep(30)"
    source, pid_file = _script(tmp_path, repr([detached, ordinary]), "time.sleep(30)")
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        adapter._run_subprocess([sys.executable, "-c", source], str(tmp_path))
    assert exc.value.silence_halted is True
    pids = [int(pid) for pid in open(pid_file).read().split()]
    _wait_dead(pids, str(tmp_path / "descendant-marker"))


def test_normal_exit_cleans_stragglers_without_delay(tmp_path):
    adapter = AGYAdapter(workspace=tmp_path, timeout_seconds=5)
    source, pid_file = _script(
        tmp_path,
        repr(["import time; time.sleep(30)"]),
        "pass",
    )
    started = time.monotonic()
    adapter._run_subprocess([sys.executable, "-c", source], str(tmp_path))
    assert time.monotonic() - started < 1
    pids = [int(pid) for pid in open(pid_file).read().split()]
    _wait_dead(pids, str(tmp_path / "descendant-marker"))
