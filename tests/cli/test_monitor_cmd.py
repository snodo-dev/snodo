"""Tests for `snodo monitor` — the read-only live view.

FILE: tests/cli/test_monitor_cmd.py

The monitor renders what the engine already writes (.snodo/audit.log,
.snodo/tasks/<id>/state.json, .snodo/jobs/<id>/state.json, worktrees). These
tests pin the contract that makes that safe next to a running process:

- it renders from fixture state with no engine running,
- a partially written state file or a torn audit tail is tolerated, never fatal,
- reading never takes a lock (flock) that a concurrent append would block on,
- the monitor writes nothing at all,
- a foreground run records its pid in the task state file at start, so
  liveness is answerable the same way it already is for a job.
"""

import fcntl
import inspect
import json
import os
import subprocess
import sys
import time
from datetime import datetime, UTC
from types import SimpleNamespace

import pytest
import typer
from rich.console import Console

from snodo.cli.commands import monitor_cmd
from snodo.cli.commands.monitor_cmd import (
    RunRow,
    build_renderable,
    collect_snapshot,
    read_json_state,
    register,
    tail_audit_events,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path):
    """An initialized project root with empty tasks/ and jobs/ dirs."""
    snodo = tmp_path / ".snodo"
    (snodo / "tasks" / "task_alpha").mkdir(parents=True)
    (snodo / "jobs" / "j_alpha").mkdir(parents=True)
    return tmp_path


def _audit_line(seq, event_type, task_ref, ts, **data):
    payload = {
        "sequence": seq,
        "timestamp": datetime.fromtimestamp(ts, UTC).isoformat(),
        "event_type": event_type,
        "project_id": "p_test",
        "data": {"op": event_type, "task_ref": task_ref, **data},
        "previous_hash": "0" * 64,
        "event_hash": "1" * 64,
    }
    return json.dumps(payload)


def _write_audit(root, lines):
    (root / ".snodo" / "audit.log").write_text("".join(line + "\n" for line in lines))


def _write_task_state(root, task_id, state):
    d = root / ".snodo" / "tasks" / task_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(state))


def _render_text(snapshot, width=200):
    """Render a snapshot to plain text (what the operator would see)."""
    from rich.console import Console

    console = Console(width=width, record=True)
    console.print(build_renderable(snapshot))
    return console.export_text()


@pytest.fixture
def snapshot_project(project):
    """Fixture state mid-run: pre-validated, coder dispatched, two calls landed."""
    now = time.time()
    _write_audit(project, [
        _audit_line(0, "transition", "task_alpha", now - 900, from_mode="producer", to_mode="producer"),
        _audit_line(1, "validate", "task_alpha", now - 800, phase="pre_execute", outcome="passed"),
        _audit_line(2, "dispatch", "task_alpha", now - 600, coder="litellm"),
    ])
    _write_task_state(project, "task_alpha", {
        "task_id": "task_alpha",
        "description": "add the monitor",
        "status": "running",
        "started_at": now - 900,
        "pid": os.getpid(),
        "usage": [
            {"timestamp": now - 300, "role": "coder", "model": "gpt-4o",
             "cost": 0.02, "total_tokens": 500, "duration_ms": 4200},
            {"timestamp": now - 60, "role": "coder", "model": "gpt-4o",
             "cost": 0.03, "total_tokens": 800, "duration_ms": 5100},
        ],
    })
    return project


# ---------------------------------------------------------------------------
# 1. Registration + rendering from fixture state, no engine running
# ---------------------------------------------------------------------------


def test_monitor_registered():
    app = typer.Typer()
    register(app)
    names = [c.name or c.callback.__name__ for c in app.registered_commands]
    assert "monitor" in names


def test_renders_from_fixture_state(snapshot_project, capsys, monkeypatch):
    """The view renders with no engine running: tasks, phases, idle, cost."""
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(
        "snodo.infrastructure.paths.require_project_root", lambda: str(snapshot_project),
    )
    rc = monitor_cmd.monitor_command(SimpleNamespace(interval=2.0, once=True, json=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "snodo monitor" in out
    assert "task_alpha" in out
    # phase columns answer the operator's questions at a glance
    assert "SINCE LAST SIGN OF LIFE" in out
    assert "PHASE FOR" in out
    assert "ALIVE?" in out
    # cost/tokens come from the usage records
    assert "$0.0500" in out


def test_snapshot_assembles_phase_and_idle(snapshot_project):
    snap = collect_snapshot(str(snapshot_project))
    rows = {r.run_id: r for r in snap["runs"]}
    row = rows["task_alpha"]
    # last sign of life is the newest usage record
    assert row.idle_seconds(snap["now"]) == pytest.approx(60.0, abs=1.0)
    # two consecutive coder calls are one phase, running since the first
    phase, since = row.phase_group(snap["now"])
    assert phase == "execute"
    assert snap["now"] - since == pytest.approx(300.0, abs=1.0)
    assert row.usage_records == 2
    assert row.cost_total == pytest.approx(0.05)
    assert row.alive() is True  # pid == this test process


def test_snapshot_dead_pid_is_visible(project):
    now = time.time()
    _write_audit(project, [_audit_line(0, "validate", "task_alpha", now - 30, phase="pre_execute")])
    _write_task_state(project, "task_alpha", {
        "task_id": "task_alpha", "status": "running", "started_at": now - 300,
        "pid": 999_999_999,  # near-certainly nonexistent
        "usage": [],
    })
    snap = collect_snapshot(str(project))
    row = {r.run_id: r for r in snap["runs"]}["task_alpha"]
    assert row.status == "running"
    assert row.alive() is False
    assert "DEAD" in _render_text(snap)


def test_snapshot_missing_pid_is_honestly_unknown(project):
    """A task started before pids were recorded cannot answer liveness — say so."""
    _write_task_state(project, "task_legacy", {
        "task_id": "task_legacy", "status": "running", "started_at": time.time() - 10,
        "usage": [],
    })
    snap = collect_snapshot(str(project))
    row = {r.run_id: r for r in snap["runs"]}["task_legacy"]
    assert row.pid is None
    assert row.alive() is None
    assert "pid not recorded" in _render_text(snap)


def test_blind_coder_phase_says_so(project):
    """ADR 034 window: dispatched coder with no usage record yet is blind, not stalled."""
    now = time.time()
    _write_audit(project, [
        _audit_line(0, "validate", "task_alpha", now - 400, phase="pre_execute"),
        _audit_line(1, "token_consumed", "task_alpha", now - 399),
    ])
    _write_task_state(project, "task_alpha", {
        "task_id": "task_alpha", "status": "running", "started_at": now - 400,
        "pid": os.getpid(), "usage": [],
    })
    snap = collect_snapshot(str(project))
    row = {r.run_id: r for r in snap["runs"]}["task_alpha"]
    assert row.phase_group(snap["now"])[0] == "execute"
    assert monitor_cmd._in_place_blind(row, snap["now"]) is True
    rendered = _render_text(snap)
    assert "blind" in rendered
    assert "ADR 034" in rendered


# ---------------------------------------------------------------------------
# 2. Partially written state is tolerated, never fatal
# ---------------------------------------------------------------------------


def test_partial_state_file_tolerated(project):
    now = time.time()
    _write_task_state(project, "task_alpha", {"task_id": "task_alpha", "status": "running",
                                              "started_at": now, "usage": []})
    job = project / ".snodo" / "jobs" / "j_alpha"
    job.mkdir(parents=True, exist_ok=True)
    (job / "state.json").write_text('{"status": "runn')  # truncated mid-write
    snap = collect_snapshot(str(project))  # must not raise
    rows = {r.run_id: r for r in snap["runs"]}
    assert rows["j_alpha"].state_readable is False
    assert any("unreadable" in n for n in snap["notes"])
    assert "unreadable" in _render_text(snap)


def test_read_json_state_returns_none_on_garbage(tmp_path):
    p = tmp_path / "state.json"
    p.write_text('{"a": ')
    assert read_json_state(p) is None
    assert read_json_state(tmp_path / "missing.json") is None


def test_torn_audit_tail_tolerated(snapshot_project):
    """A half-written trailing audit line (mid-append) is skipped, not fatal."""
    log = snapshot_project / ".snodo" / "audit.log"
    with open(log, "a") as f:
        f.write('{"sequence": 9, "timestamp": "20')  # engine mid-append
    events, notes = tail_audit_events(log)
    assert [e["sequence"] for e in events] == [0, 1, 2]
    assert any("in-flight" in n for n in notes)
    snap = collect_snapshot(str(snapshot_project))  # the whole path stays alive
    assert snap["runs"]


def test_malformed_audit_line_counted_not_fatal(snapshot_project):
    log = snapshot_project / ".snodo" / "audit.log"
    lines = log.read_text().splitlines()
    lines.insert(1, "this is not json at all")
    log.write_text("\n".join(lines) + "\n")
    events, notes = tail_audit_events(log)
    assert len(events) == 3
    assert any("malformed" in n for n in notes)


# ---------------------------------------------------------------------------
# 3. Reading never acquires a lock that would block a concurrent append
# ---------------------------------------------------------------------------

_LOCK_HOLDER = """
import fcntl, sys, time
path = sys.argv[1]
f = open(path, "a")
fcntl.flock(f.fileno(), fcntl.LOCK_EX)
print("locked", flush=True)
time.sleep(float(sys.argv[2]))
"""


def test_reader_does_not_use_flock():
    """A monitor that flocks would stall the run it watches. It must not.

    Behavioral tests below prove the property; this one pins the mechanism:
    the module never references flock at all.
    """
    source = inspect.getsource(monitor_cmd)
    assert "flock(" not in source
    assert "import fcntl" not in source


def test_reads_succeed_while_a_writer_holds_the_exclusive_lock(snapshot_project):
    """Engine-style append under LOCK_EX must not block (nor be blocked by) the reader."""
    snodo = snapshot_project / ".snodo"
    holder = subprocess.Popen(
        [sys.executable, "-c", _LOCK_HOLDER, str(snodo / "audit.log"), "5"],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        start = time.monotonic()
        snap = collect_snapshot(str(snapshot_project))
        assert time.monotonic() - start < 2.0
        assert snap["runs"], "snapshot should still read everything"
        # And the writer's exclusive lock is still exclusively held — the
        # reader neither broke it nor queued behind it.
        probe = open(snodo / "audit.log", "a")
        with pytest.raises(BlockingIOError):
            fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        probe.close()
    finally:
        holder.terminate()
        holder.wait(timeout=5)


def test_continuous_reading_never_stalls_a_writing_engine(tmp_path):
    """Interleave a real writer with continuous snapshot reads for ~3s.

    The engine's append path waits at most ``_LOCK_TIMEOUT`` (10s) for its
    exclusive lock and then refuses to write — so if the reader ever shared a
    lock with the writer, this loop is where a run would visibly stall.
    """
    from snodo.infrastructure.audit import AuditLog

    snodo = tmp_path / ".snodo"
    (snodo / "tasks" / "task_alpha").mkdir(parents=True)
    log = snodo / "audit.log"
    audit = AuditLog(str(log), project_id="p_test")
    _write_task_state(tmp_path, "task_alpha", {
        "task_id": "task_alpha", "status": "running",
        "started_at": time.time(), "pid": os.getpid(), "usage": [],
    })

    deadline = time.monotonic() + 3.0
    appends = 0
    reads = 0
    while time.monotonic() < deadline:
        started = time.monotonic()
        audit.append_event("validate", {"op": "validate", "task_ref": "task_alpha",
                                        "phase": "pre_execute"})
        assert time.monotonic() - started < 1.0, "append stalled — reader is holding a lock"
        appends += 1
        collect_snapshot(str(tmp_path))
        reads += 1
    assert appends >= 3 and reads >= 3
    assert audit.verify_chain()


def test_writer_can_still_lock_after_a_read(snapshot_project):
    """After collect_snapshot returns, an append must acquire LOCK_EX at once.

    A reader that left a shared lock held (or never released) would make this
    block; the engine's append path waits at most 10s and then refuses to
    write, so a sticky reader would surface as a stalled or failed run.
    """
    collect_snapshot(str(snapshot_project))
    log = snapshot_project / ".snodo" / "audit.log"
    with open(log, "a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # must not block
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def test_engine_append_path_still_works_after_reads(tmp_path):
    """The engine's own append path runs right after a snapshot with no stall.

    Uses a real ``AuditLog`` (valid chain) rather than fixture lines: the
    append is the thing the reader must never obstruct, and the engine waits
    at most 10s for ``flock`` before refusing to write.
    """
    from snodo.infrastructure.audit import AuditLog

    snodo = tmp_path / ".snodo"
    snodo.mkdir()
    log = snodo / "audit.log"
    audit = AuditLog(str(log), project_id="p_test")
    audit.append_event("validate", {"op": "validate", "task_ref": "task_alpha",
                                    "phase": "pre_execute"})
    collect_snapshot(str(tmp_path))
    Console(width=140).print(build_renderable(collect_snapshot(str(tmp_path))))
    started = time.monotonic()
    audit.append_event("dispatch", {"op": "dispatch", "task_ref": "task_alpha"})
    assert time.monotonic() - started < 2.0
    assert audit.verify_chain()


def test_task_state_reads_dont_touch_the_state_lock_file(snapshot_project):
    """Engine state writes flock .state.json.lock; the reader must not block on it."""
    lock = snapshot_project / ".snodo" / "tasks" / "task_alpha" / ".state.json.lock"
    lock.touch()
    holder = subprocess.Popen(
        [sys.executable, "-c", _LOCK_HOLDER, str(lock), "5"],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        start = time.monotonic()
        snap = collect_snapshot(str(snapshot_project))
        assert time.monotonic() - start < 2.0
        assert any(r.run_id == "task_alpha" for r in snap["runs"])
    finally:
        holder.terminate()
        holder.wait(timeout=5)


# ---------------------------------------------------------------------------
# 4. The monitor is a pure reader — it appears nowhere in the chain
# ---------------------------------------------------------------------------


def test_monitor_writes_nothing(snapshot_project):
    """No audit event, no new file, no modified byte: the chain never sees it."""
    state_before = {
        p: (p.stat().st_mtime_ns, p.read_bytes())
        for p in sorted(snapshot_project.rglob("*")) if p.is_file()
    }
    log = snapshot_project / ".snodo" / "audit.log"
    lines_before = len(log.read_text().splitlines())

    snap = collect_snapshot(str(snapshot_project))
    _render_text(snap)

    # The chain grew by nothing: the monitor observes and does not appear.
    assert len(log.read_text().splitlines()) == lines_before
    state_after = {
        p: (p.stat().st_mtime_ns, p.read_bytes())
        for p in sorted(snapshot_project.rglob("*")) if p.is_file()
    }
    assert state_after == state_before
    assert snap["runs"]


# ---------------------------------------------------------------------------
# 5. A foreground run records its pid in the task state file at start
# ---------------------------------------------------------------------------


def test_foreground_run_records_pid(tmp_path, monkeypatch):
    """`snodo run` must leave liveness on disk, same as a job's wrapper does."""
    from snodo.cli.commands.run_cmd import _record_task_start

    (tmp_path / ".snodo").mkdir()
    _record_task_start(str(tmp_path), "task_alpha", "do the thing")
    state = json.loads((tmp_path / ".snodo" / "tasks" / "task_alpha" / "state.json").read_text())
    assert state["pid"] == os.getpid()
    assert state["status"] == "running"
    assert state["started_at"]


def test_monitor_uses_recorded_pid_for_liveness(tmp_path):
    """End to end: run records pid -> monitor answers alive/DEAD from it."""
    from snodo.cli.commands.run_cmd import _record_task_start

    (tmp_path / ".snodo").mkdir()
    _record_task_start(str(tmp_path), "task_alpha", "do the thing")
    snap = collect_snapshot(str(tmp_path))
    row = {r.run_id: r for r in snap["runs"]}["task_alpha"]
    assert row.pid == os.getpid()
    assert row.alive() is True


# ---------------------------------------------------------------------------
# --json + live-loop smoke
# ---------------------------------------------------------------------------


def test_monitor_json_snapshot(snapshot_project, capsys, monkeypatch):
    monkeypatch.setattr(
        "snodo.infrastructure.paths.require_project_root", lambda: snapshot_project,
    )
    rc = monitor_cmd.monitor_command(SimpleNamespace(interval=1, once=True, json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "snodo.monitor.v1"
    rows = {r["run_id"]: r for r in payload["runs"]}
    assert rows["task_alpha"]["phase"] == "execute"
    assert rows["task_alpha"]["idle_seconds"] == pytest.approx(60.0, abs=1.0)


def test_monitor_live_loop_refresh_and_exit(snapshot_project, monkeypatch):
    """The timer loop re-collects on every refresh and stops on Ctrl-C."""
    calls = {"n": 0}
    real_collect = monitor_cmd.collect_snapshot

    def _collect(*a, **k):
        calls["n"] += 1
        if calls["n"] >= 3:
            raise KeyboardInterrupt
        return real_collect(*a, **k)

    monkeypatch.setattr(monitor_cmd, "collect_snapshot", _collect)
    monkeypatch.setattr(monitor_cmd.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        "snodo.infrastructure.paths.require_project_root", lambda: snapshot_project,
    )
    rc = monitor_cmd.monitor_command(SimpleNamespace(interval=0.1, once=False, json=False))
    assert rc == 0
    assert calls["n"] >= 3  # initial frame + at least two timer refreshes


# ---------------------------------------------------------------------------
# Phase mapping (marker -> the phase it started)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label,phase", [
    ("validate:pre_execute", "execute"),
    ("coder", "execute"),
    ("dispatch", "post-validate"),
    ("validate:post_execute", "route"),
    ("post_validation_route", "route"),
    ("validator:quality", "validate"),
    ("halt", "halted"),
    ("transition", "transition"),
])
def test_marker_phase_mapping(label, phase):
    assert monitor_cmd._phase_for(label) == phase


def test_dual_written_usage_is_flagged(project):
    """A job and its task both carry the same usage records — the view says so.

    ``usage_tracker`` appends each LLM call to both the job's and the task's
    state.json, so the two rows each show a complete cost. Readable as a
    running total that would double-count; the notes flag the pairing.
    """
    now = time.time()
    record = {"timestamp": now - 5, "role": "coder", "model": "gpt-4o",
              "cost": 0.10, "total_tokens": 400, "duration_ms": 900}
    _write_task_state(project, "task_alpha", {
        "task_id": "task_alpha", "status": "running", "started_at": now - 60,
        "pid": os.getpid(), "usage": [record],
    })
    job = project / ".snodo" / "jobs" / "j_alpha"
    job.mkdir(parents=True, exist_ok=True)
    (job / "state.json").write_text(json.dumps(
        {"status": "running", "pid": os.getpid(), "started_at": now - 60, "usage": [record]}
    ))
    (job / "task.json").write_text(json.dumps(
        {"task_id": "task_alpha", "description": "wrapped", "coder": "litellm"}
    ))
    snap = collect_snapshot(str(project))
    assert any("dual-written" in n for n in snap["notes"])


def test_runrow_idle_and_cost():
    now = time.time()
    row = RunRow(
        kind="task", run_id="task_x", description="", status="running",
        pid=None, started_at=now - 100,
        markers=[monitor_cmd.PhaseMarker(ts=now - 30, source="usage", label="coder")],
        usage_records=1, cost_total=0.5,
    )
    assert row.idle_seconds(now) == pytest.approx(30.0)
    assert monitor_cmd._fmt_cost(row) == "$0.5000"
