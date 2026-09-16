"""The remote gate's process lifetime (Fixes #304).

A gate runs over a terminal so a hangup reaches the remote process group, and
``scripts/gate_remote.sh`` is what catches that hangup and tears the group down
— pytest and every worker with it. These tests exercise that file and the
Makefile wiring around it, not the gate host, and hold the two promises the fix
makes:

* a normal gate's exit status and output are unchanged; and
* an interrupted gate leaves none of its process tree running.

A canary proves the lifetime test can fail: a supervisor with no trap, hung up
the same way, leaves the descendant it spawned behind. The fast cases run a
probe rather than the suite, so each costs a process and a signal; one case
runs a real ``pytest -n`` to prove the tree pytest actually builds goes down.
"""

from __future__ import annotations

import itertools
import os
import pty
import re
import select
import signal
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE_REMOTE = ROOT / "scripts" / "gate_remote.sh"
MAKEFILE = ROOT / "Makefile"

_counter = itertools.count()


def _unique_sleep() -> str:
    """A sleep duration no other process on the machine shares.

    The probe's child is found by its duration alone, so a collision with some
    unrelated sleep would make the survivor count a lie in either direction.
    """
    return str(6_000_000 + os.getpid() % 100_000 + next(_counter) * 7)


def _bash(script: str, *, session: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        start_new_session=session,
    )


def _pids_matching(pattern: str) -> set[int]:
    """PIDs whose full command line matches *pattern* (a regex for pgrep -f)."""
    out = subprocess.run(
        ["pgrep", "-f", pattern], capture_output=True, text=True
    ).stdout
    return {int(pid) for pid in out.split()}


def _wait_until_gone(pattern: str, timeout: float = 10.0) -> set[int]:
    """Wait for every match to exit; return whatever is still alive after."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        survivors = _pids_matching(pattern)
        if not survivors:
            return survivors
        time.sleep(0.1)
    return _pids_matching(pattern)


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _supervise_command(command: list[str], slot_root: Path) -> str:
    """A bash program that sources the real wrapper and supervises *command*."""
    argv = " ".join(f'"{part}"' for part in command)
    return (
        f'source "{GATE_REMOTE}"\n'
        f'export GATE_ROOT="{slot_root}" GATE_SLOTS=2\n'
        f"gate_supervise {argv}\n"
    )


def _start(program: str) -> subprocess.Popen:
    return subprocess.Popen(
        ["bash", "-c", program],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _run_on_raw_pty(program: str, tmp_path: Path) -> bytes:
    """Run *program* with its stdin/stdout/stderr on a raw-mode pty.

    ``ssh -tt`` does not hand the remote command a cooked terminal: it copies
    the operator's raw termios onto the pty, which clears ``opost``. Running the
    probe this way reproduces exactly that, so a wrapper that leaves
    post-processing off emits bare LFs.
    """
    master, slave = pty.openpty()
    attrs = termios.tcgetattr(slave)
    attrs[1] &= ~termios.OPOST
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    env = dict(os.environ, GATE_ROOT=str(tmp_path), GATE_SLOTS="2")
    proc = subprocess.Popen(
        ["bash", "-c", program],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        start_new_session=True,
    )
    os.close(slave)
    output = b""
    try:
        while True:
            ready, _, _ = select.select([master], [], [], 5.0)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
    finally:
        _kill_group(proc.pid)
        proc.wait(timeout=5)
        os.close(master)
    return output


def _await_child(pattern: str, proc: subprocess.Popen, timeout: float = 10.0) -> None:
    """Wait until the probe's child exists, or fail with the wrapper's output."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _pids_matching(pattern):
            return
        if proc.poll() is not None:
            _, err = proc.communicate(timeout=5)
            pytest.fail(f"gate_supervise exited before the probe ran: {err!r}")
        time.sleep(0.1)
    pytest.fail("the probe's child never started")


@pytest.fixture
def probe(tmp_path: Path):
    """A gate-like command: a leader that spawns a child, then loops.

    The child is the point. A wrapper that only kills its immediate command
    leaves the child running — exactly the orphaned worker the ticket is about
    — so the assertion looks for the child, not the leader.
    """
    sleep_arg = _unique_sleep()
    script = tmp_path / "probe.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "echo PROBE_START\n"
        f"sleep {sleep_arg} &\n"
        "wait\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    yield script, sleep_arg
    subprocess.run(["pkill", "-9", "-f", f"sleep {sleep_arg}"], capture_output=True)


# --- the normal gate is untouched -------------------------------------------


@pytest.mark.parametrize("status", [0, 7])
def test_gate_supervise_carries_the_exit_status(tmp_path: Path, status: int):
    """A failing check still fails the gate; pipefail keeps `cat` from masking it."""
    proc = _bash(
        f'source "{GATE_REMOTE}"\n'
        f'export GATE_ROOT="{tmp_path}" GATE_SLOTS=2\n'
        f'gate_supervise bash -c "exit {status}"'
    )
    assert proc.returncode == status, (
        f"gate_supervise turned exit {status} into {proc.returncode}; "
        "a failed check would report success"
    )


def test_gate_supervise_leaves_output_byte_for_byte_unchanged(tmp_path: Path):
    """The pty must not colour or re-terminate the checks' output.

    The same bytes piped through the wrapper and written straight to a pipe
    must match: if the terminal leaked through, the output would grow CRs and
    colour escapes and a clean gate would print something new.
    """
    program = r"printf 'first\nsecond\n'; printf 'err\n' 1>&2"
    plain = _bash(program)
    supervised = _bash(
        f'source "{GATE_REMOTE}"\n'
        f'export GATE_ROOT="{tmp_path}" GATE_SLOTS=2\n'
        f"gate_supervise {program}"
    )
    assert supervised.returncode == plain.returncode
    assert supervised.stdout == plain.stdout, (
        "the wrapper changed the gate's stdout; a clean gate must print what "
        "it always did"
    )


def test_gate_output_lines_begin_at_column_zero_on_a_raw_pty(tmp_path: Path):
    """A gate's output must land in rows, not march off as a staircase.

    ``ssh -tt`` copies the operator's raw termios onto the remote pty, clearing
    ``opost``. With ``opost`` off, ``onlcr`` does nothing and a bare LF moves
    down a row without returning to column zero, so every line starts where the
    last one ended. The wrapper must turn output post-processing back on: each
    line below has to be followed by CR before LF.
    """
    program = r"printf 'one\ntwo\nthree\n'"
    output = _run_on_raw_pty(
        f'source "{GATE_REMOTE}"\n'
        f'export GATE_ROOT="{tmp_path}" GATE_SLOTS=2\n'
        f"gate_supervise {program}",
        tmp_path,
    )
    assert output == b"one\r\ntwo\r\nthree\r\n", (
        "a gate's output staircased on a raw terminal: each line must return "
        f"to column zero before the next (got {output!r})"
    )


def test_column_zero_setting_is_not_silently_swallowed():
    """The setting the output's readability depends on must not be hidden.

    The staircase fix was a no-op behind ``2>/dev/null || true``: a failure
    there left every line starting where the last one ended, and nothing said
    so. If the call is still made, it must not swallow its own failure.
    """
    text = GATE_REMOTE.read_text(encoding="utf-8")
    assert "stty opost onlcr" in text, (
        "the wrapper must turn output post-processing back on; onlcr alone is "
        "inert while opost is off, which is the state ssh -tt leaves the pty in"
    )
    for line in text.splitlines():
        if "stty" in line and "opost" in line:
            assert "2>/dev/null" not in line, (
                "the setting the output depends on must not discard its error"
            )
            assert "|| true" not in line, (
                "the setting the output depends on must not swallow its failure"
            )


# --- the interrupted gate leaves nothing behind -----------------------------


@pytest.mark.parametrize("sig", [signal.SIGHUP, signal.SIGINT, signal.SIGTERM])
def test_interrupted_gate_leaves_no_descendant(probe, tmp_path: Path, sig: int):
    """A hangup — or an interrupt, or a kill — takes the whole group down.

    The signal goes to the supervising shell the way a pty hangup reaches it:
    the shell is a session leader, and its trap forwards the signal to the
    process group (the gate *and* the child it spawned).
    """
    probe_script, sleep_arg = probe
    proc = _start(_supervise_command(["bash", str(probe_script)], tmp_path))
    try:
        _await_child(f"sleep {sleep_arg}", proc)
        os.kill(proc.pid, sig)
        survivors = _wait_until_gone(f"sleep {sleep_arg}")
        assert not survivors, (
            f"SIG{sig.name} left the gate's child running ({sorted(survivors)}); "
            "a remote command that outlives its client is the fault"
        )
    finally:
        _kill_group(proc.pid)
        proc.wait(timeout=5)


def test_interrupted_xdist_run_leaves_no_pytest_or_worker(probe, tmp_path: Path):
    """The tree a real pytest -n builds is the tree that must die (Fixes #304).

    The probe here is the same pytest the gate runs, scoped to one generated
    test that spawns a uniquely named child and then sleeps. Interrupting the
    supervised run must leave neither the child nor a pytest process for this
    run behind — the controller and its xdist workers are what saturated the
    host when an interrupted gate orphaned them.
    """
    _, sleep_arg = probe
    run_dir = tmp_path / "xdist-run"
    run_dir.mkdir()
    (run_dir / "test_slow_probe.py").write_text(
        "import subprocess, time\n"
        "\n"
        "def test_slow_probe():\n"
        f"    subprocess.Popen(['sleep', '{sleep_arg}'])\n"
        "    time.sleep(120)\n",
        encoding="utf-8",
    )
    marker = re.escape(str(run_dir))
    proc = _start(
        _supervise_command(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "-n",
                "2",
                "-q",
                str(run_dir),
            ],
            tmp_path,
        )
    )
    try:
        _await_child(f"sleep {sleep_arg}", proc, timeout=30.0)
        os.kill(proc.pid, signal.SIGTERM)
        assert not _wait_until_gone(f"sleep {sleep_arg}"), (
            "an interrupted xdist run left a test's child running"
        )
        assert not _wait_until_gone(marker), (
            "an interrupted xdist run left a pytest process for this run running"
        )
    finally:
        _kill_group(proc.pid)
        subprocess.run(["pkill", "-9", "-f", f"sleep {sleep_arg}"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", marker], capture_output=True)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def test_canary_a_supervisor_with_no_trap_leaks_the_descendant(probe, tmp_path: Path):
    """Canary (#58): prove the lifetime test can tell the fix from the fault.

    The same probe, hung up the same way, under a supervisor with the trap
    removed, must leave its child behind. If this ever stops failing, the test
    above has stopped testing anything.
    """
    probe_script, sleep_arg = probe
    naive = (
        f'"{probe_script}" &\n'
        "gate_pid=$!\n"
        'wait "$gate_pid"\n'
    )
    proc = _start(naive)
    try:
        _await_child(f"sleep {sleep_arg}", proc)
        os.kill(proc.pid, signal.SIGHUP)
        proc.wait(timeout=5)
        time.sleep(0.5)
        survivors = _pids_matching(f"sleep {sleep_arg}")
        assert survivors, (
            "a supervisor with no trap was expected to orphan the probe's "
            "child; the lifetime test would no longer be able to fail"
        )
    finally:
        _kill_group(proc.pid)
        subprocess.run(["pkill", "-9", "-f", f"sleep {sleep_arg}"], capture_output=True)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def test_a_detached_child_is_reaped_too(tmp_path: Path):
    """A child in its own session is still the gate's child.

    ``snodo.jobs.runner.spawn_background`` starts jobs with
    ``start_new_session=True``, so their process group is not the gate's and a
    plain group signal walks past them. The reaper walks the descendant tree,
    which is what reaches a child that called setsid.
    """
    sleep_arg = _unique_sleep()
    script = tmp_path / "detached_probe.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "echo PROBE_START\n"
        "python3 -c \"import subprocess,time; "
        f"subprocess.Popen(['sleep','{sleep_arg}'], start_new_session=True); "
        "time.sleep(12345)\"\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    proc = _start(_supervise_command(["bash", str(script)], tmp_path))
    try:
        _await_child(f"sleep {sleep_arg}", proc)
        os.kill(proc.pid, signal.SIGTERM)
        survivors = _wait_until_gone(f"sleep {sleep_arg}")
        assert not survivors, (
            "a detached child survived the hangup; a gate whose jobs daemonise "
            f"would still leak after an interrupt ({sorted(survivors)})"
        )
    finally:
        _kill_group(proc.pid)
        subprocess.run(["pkill", "-9", "-f", f"sleep {sleep_arg}"], capture_output=True)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


# --- the Makefile asks for the mechanism the wrapper relies on --------------


def test_gate_targets_run_the_wrapper_over_a_terminal():
    """The hangup only reaches the gate if `ssh` allocates the terminal.

    ``-tt`` is the mechanism: without it a killed `make` or a dropped link
    leaves the remote group with no controlling terminal to hang up. The
    wrapper is what holds the trap, and GATE_SLOTS is the bound. Losing any of
    the three silently restores the fault this fixes.
    """
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "GATE_SSH   = ssh -q -tt $(GATE_HOST)" in text, (
        "the gate must run over a terminal; without -tt a hangup never reaches "
        "the remote process group"
    )
    assert "bash scripts/gate_remote.sh gate'" in text
    assert "bash scripts/gate_remote.sh gate-ci'" in text
    assert "GATE_SLOTS ?= 2" in text, (
        "concurrent gates must be bounded so a burst cannot saturate the host"
    )
    assert "GATE_JOBS" in text
