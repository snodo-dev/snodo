"""Tests for the dashboard liveness facts (Tasks and Jobs panes).

FILE: tests/dashboard/test_liveness.py

The cockpit's Tasks and Jobs panes answer the operator's real question — is
this still alive, and how long has it been in the phase it is in — from what
the engine already records (.snodo/audit.log, .snodo/tasks/<id>/state.json,
.snodo/jobs/<id>/state.json). These tests pin the contract that makes that
safe next to a running process:

- the panes render from fixture state on disk with no engine running,
- a partially written state file or a torn audit tail is tolerated, never
  fatal to the screen,
- reading never takes a lock (flock) that a concurrent append would block on,
- a finished task whose state file still says running is not presented as live,
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

import pytest

from snodo.dashboard import liveness
from snodo.dashboard.liveness import (
    RunRow,
    collect_snapshot,
    fmt_age,
    in_place_blind,
    is_stale,
    read_json_state,
    tail_audit_events,
)
from snodo.dashboard.providers import DashboardDataProvider


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


def _write_job_state(root, job_id, state, task_id="task_alpha"):
    d = root / ".snodo" / "jobs" / job_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(state))
    (d / "task.json").write_text(json.dumps({"task_id": task_id, "description": "wrapped"}))


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
# 1. The panes render from fixture state on disk, no engine running
# ---------------------------------------------------------------------------


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


def test_provider_liveness_cells(snapshot_project):
    """The pane cells answer the operator's questions at a glance."""
    provider = DashboardDataProvider(str(snapshot_project))
    row = provider.get_task_liveness("task_alpha")
    assert row is not None
    phase, phase_for, idle, alive, cost = provider.liveness_cells(row)
    assert "execute" in phase
    assert "1m00s" in idle  # seconds since the last sign of life
    assert "alive" in alive
    assert "$0.0500" in cost


def test_provider_liveness_rows_include_jobs(snapshot_project):
    now = time.time()
    _write_job_state(snapshot_project, "j_alpha", {
        "status": "running", "pid": os.getpid(), "started_at": now - 60,
        "usage": [{"timestamp": now - 5, "role": "coder", "model": "gpt-4o",
                   "cost": 0.10, "total_tokens": 400, "duration_ms": 900}],
    })
    provider = DashboardDataProvider(str(snapshot_project))
    job = provider.get_job_liveness("j_alpha")
    assert job is not None
    assert job.kind == "job"
    assert job.linked_ref == "task_alpha"
    assert job.alive() is True


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
    provider = DashboardDataProvider(str(project))
    assert "DEAD" in provider.liveness_cells(row)[3]


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
    provider = DashboardDataProvider(str(project))
    assert "pid not recorded" in provider.liveness_cells(row)[3]


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
    assert in_place_blind(row, snap["now"]) is True
    provider = DashboardDataProvider(str(project))
    assert "blind" in provider.liveness_cells(row)[0]
    assert "ADR 034" in provider.liveness_cells(row)[0]


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
    """A dashboard that flocks would stall the run it watches. It must not.

    Behavioral tests below prove the property; this one pins the mechanism:
    the module never references flock at all.
    """
    source = inspect.getsource(liveness)
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
# 4. The dashboard is a pure reader — it appears nowhere in the chain
# ---------------------------------------------------------------------------


def test_dashboard_writes_nothing(snapshot_project):
    """No audit event, no new file, no modified byte: the chain never sees it."""
    state_before = {
        p: (p.stat().st_mtime_ns, p.read_bytes())
        for p in sorted(snapshot_project.rglob("*")) if p.is_file()
    }
    log = snapshot_project / ".snodo" / "audit.log"
    lines_before = len(log.read_text().splitlines())

    provider = DashboardDataProvider(str(snapshot_project))
    provider.get_liveness_rows()
    provider.get_liveness_notes()

    # The chain grew by nothing: the dashboard observes and does not appear.
    assert len(log.read_text().splitlines()) == lines_before
    state_after = {
        p: (p.stat().st_mtime_ns, p.read_bytes())
        for p in sorted(snapshot_project.rglob("*")) if p.is_file()
    }
    assert state_after == state_before


# ---------------------------------------------------------------------------
# 5. A finished task whose state file still says running is not presented live
# ---------------------------------------------------------------------------


def test_finished_task_with_stale_running_state_is_stale(project):
    """Days-old finished work must be named stale, never presented as running."""
    now = time.time()
    _write_task_state(project, "task_alpha", {
        "task_id": "task_alpha", "status": "running",  # never updated to completed
        "started_at": now - 3 * 86400, "pid": 999_999_999, "usage": [],
    })
    snap = collect_snapshot(str(project))
    row = {r.run_id: r for r in snap["runs"]}["task_alpha"]
    assert row.status == "running"
    assert row.alive() is False
    assert is_stale(row, snap["now"]) is True
    provider = DashboardDataProvider(str(project))
    assert provider.is_stale_row(row) is True


def test_recent_silent_running_task_is_not_stale(project):
    """A live run can be silent for a while (e.g. a long coder turn) — not stale."""
    now = time.time()
    _write_audit(project, [_audit_line(0, "dispatch", "task_alpha", now - 120, coder="litellm")])
    _write_task_state(project, "task_alpha", {
        "task_id": "task_alpha", "status": "running", "started_at": now - 200,
        "pid": os.getpid(), "usage": [],
    })
    snap = collect_snapshot(str(project))
    row = {r.run_id: r for r in snap["runs"]}["task_alpha"]
    assert is_stale(row, snap["now"]) is False


def test_live_pid_beats_a_silent_state_file(project):
    """A live pid is the strongest signal: a running process is not stale."""
    now = time.time()
    _write_task_state(project, "task_alpha", {
        "task_id": "task_alpha", "status": "running", "started_at": now - 3 * 86400,
        "pid": os.getpid(), "usage": [],
    })
    snap = collect_snapshot(str(project))
    row = {r.run_id: r for r in snap["runs"]}["task_alpha"]
    assert row.alive() is True
    assert is_stale(row, snap["now"]) is False


def test_active_session_current_task_is_not_a_liveness_signal(project):
    """The session's current_task is set at run start and never cleared, so it
    must NOT rescue a stale record — that would reproduce the very bug where
    days-old finished work appears live."""
    now = time.time()
    _write_task_state(project, "task_alpha", {
        "task_id": "task_alpha", "status": "running", "started_at": now - 3 * 86400,
        "pid": 999_999_999, "usage": [],
    })
    snap = collect_snapshot(str(project))
    row = {r.run_id: r for r in snap["runs"]}["task_alpha"]
    assert is_stale(row, snap["now"]) is True


def test_terminal_status_is_never_stale(project):
    """A completed/failed record is settled, not stale — different category."""
    now = time.time()
    _write_task_state(project, "task_alpha", {
        "task_id": "task_alpha", "status": "completed", "started_at": now - 3 * 86400,
        "pid": 999_999_999, "usage": [],
    })
    snap = collect_snapshot(str(project))
    row = {r.run_id: r for r in snap["runs"]}["task_alpha"]
    assert row.is_terminal() is True
    assert is_stale(row, snap["now"]) is False


def test_queued_job_is_not_stale(project):
    """A queued job has no pid and no markers yet — it is waiting, not stale."""
    now = time.time()
    _write_job_state(project, "j_alpha", {
        "status": "queued", "pid": None, "started_at": None,
        "created_at": now - 3 * 86400, "usage": [],
    })
    snap = collect_snapshot(str(project))
    row = {r.run_id: r for r in snap["runs"]}["j_alpha"]
    assert row.status == "queued"
    assert is_stale(row, snap["now"]) is False


# ---------------------------------------------------------------------------
# 6. A foreground run records its pid in the task state file at start
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


def test_dashboard_uses_recorded_pid_for_liveness(tmp_path):
    """End to end: run records pid -> dashboard answers alive/DEAD from it."""
    from snodo.cli.commands.run_cmd import _record_task_start

    (tmp_path / ".snodo").mkdir()
    _record_task_start(str(tmp_path), "task_alpha", "do the thing")
    provider = DashboardDataProvider(str(tmp_path))
    row = provider.get_task_liveness("task_alpha")
    assert row is not None
    assert row.pid == os.getpid()
    assert row.alive() is True


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
    assert liveness._phase_for(label) == phase


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
    _write_job_state(project, "j_alpha", {
        "status": "running", "pid": os.getpid(), "started_at": now - 60, "usage": [record],
    })
    snap = collect_snapshot(str(project))
    assert any("dual-written" in n for n in snap["notes"])


def test_runrow_idle_and_cost():
    now = time.time()
    row = RunRow(
        kind="task", run_id="task_x", description="", status="running",
        pid=None, started_at=now - 100,
        markers=[liveness.PhaseMarker(ts=now - 30, source="usage", label="coder")],
        usage_records=1, cost_total=0.5,
    )
    assert row.idle_seconds(now) == pytest.approx(30.0)
    assert fmt_age(30) == "30s"


# ---------------------------------------------------------------------------
# 7. The cockpit panes render from fixture state, no engine running
# ---------------------------------------------------------------------------


def _cockpit_fixture(tmp_path, monkeypatch):
    """Fixture state on disk: a session, a wave, a task, a job, an audit tail."""
    snodo = tmp_path / ".snodo"
    (snodo / "tasks" / "task_alpha").mkdir(parents=True)
    (snodo / "jobs" / "j_alpha").mkdir(parents=True)
    (snodo / "plans" / "main").mkdir(parents=True)
    now = time.time()
    _write_audit(tmp_path, [
        _audit_line(0, "validate", "task_alpha", now - 800, phase="pre_execute", outcome="passed"),
        _audit_line(1, "dispatch", "task_alpha", now - 600, coder="litellm"),
    ])
    _write_task_state(tmp_path, "task_alpha", {
        "task_id": "task_alpha", "description": "add liveness", "status": "running",
        "started_at": now - 900, "pid": os.getpid(),
        "usage": [{"timestamp": now - 60, "role": "coder", "model": "gpt-4o",
                   "cost": 0.03, "total_tokens": 800, "duration_ms": 5100}],
    })
    _write_job_state(tmp_path, "j_alpha", {
        "status": "running", "pid": os.getpid(), "started_at": now - 60,
        "usage": [{"timestamp": now - 5, "role": "coder", "model": "gpt-4o",
                   "cost": 0.10, "total_tokens": 400, "duration_ms": 900}],
    })
    (snodo / "jobs" / "j_alpha" / "stdout.log").write_text("building\n")
    (snodo / "jobs" / "j_alpha" / "stderr.log").write_text("")
    (snodo / "wave.json").write_text(json.dumps(
        [{"wave_id": "w_0001", "feature_description": "liveness", "task_ids": ["task_alpha"]}]
    ))
    (snodo / "plans" / "main" / "status.json").write_text(json.dumps({
        "tasks": {"task_alpha": {"status": "in_progress", "parent_task_ref": None, "depth": 0}}
    }))

    # An isolated home so the session manager writes nowhere real.
    monkeypatch.setenv("SNODO_HOME", str(tmp_path / "home"))
    from snodo.infrastructure.session import SessionManager
    mgr = SessionManager()
    sess = mgr.create_session("producer", str(tmp_path))
    mgr.set_current_task(sess.session_id, "task_alpha")
    (snodo / "state.json").write_text(json.dumps({
        "current_mode": "producer",
        "active_session": {"producer": sess.session_id},
        "metadata": {},
    }))
    return tmp_path


def test_cockpit_panes_render_liveness_from_fixture_state(tmp_path, monkeypatch):
    """The Tasks and Jobs panes render the liveness facts with no engine running."""
    import asyncio

    from snodo.dashboard.app import SnodoDashboard
    from snodo.dashboard.panels.cockpit import CockpitScreen

    project = _cockpit_fixture(tmp_path, monkeypatch)

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, CockpitScreen)
            await pilot.pause(0.2)
            tasks = app.screen.query_one("#tasks-table")
            jobs = app.screen.query_one("#jobs-table")
            assert [c.label.plain for c in tasks.columns.values()] == [
                "Task ID", "Wave", "Status", "Phase", "Phase For", "Idle", "Alive?", "Cost",
            ]
            assert [c.label.plain for c in jobs.columns.values()] == [
                "Job ID", "Status", "Phase", "Phase For", "Idle", "Alive?", "Cost",
            ]
            assert tasks.row_count == 1
            assert jobs.row_count == 1
            task_row = tasks.get_row(next(iter(tasks.rows)))
            job_row = jobs.get_row(next(iter(jobs.rows)))
            # After adding Wave column at index 1, indices shift:
            # Task ID=0, Wave=1, Status=2, Phase=3, Phase For=4, Idle=5, Alive?=6, Cost=7
            assert task_row[3] == "execute"  # Phase (was index 2)
            assert "alive" in task_row[6]    # Alive? (was index 5)
            assert "$0.0300" in task_row[7]  # Cost (was index 6)
            # Jobs table columns: Job ID=0, Status=1, Phase=2, Phase For=3, Idle=4, Alive?=5, Cost=6
            assert job_row[2] == "execute"  # Phase
            assert "alive" in job_row[5]    # Alive?
            assert "$0.1000" in job_row[6]  # Cost
            # every cell must parse as rich markup (what DataTable does at render)
            from rich.text import Text
            for table in (tasks, jobs):
                for rk in table.rows:
                    for cell in table.get_row(rk):
                        Text.from_markup(str(cell), end="")

    asyncio.run(_run())


def test_cockpit_marks_stale_running_task_not_live(tmp_path, monkeypatch):
    """A finished task whose state file still says running is named stale."""
    import asyncio

    from snodo.dashboard.app import SnodoDashboard
    from snodo.dashboard.panels.cockpit import CockpitScreen

    project = _cockpit_fixture(tmp_path, monkeypatch)
    now = time.time()
    # Rewrite the task as days-old finished work that was never marked complete.
    _write_task_state(project, "task_alpha", {
        "task_id": "task_alpha", "description": "old work", "status": "running",
        "started_at": now - 3 * 86400, "pid": 999_999_999, "usage": [],
    })

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, CockpitScreen)
            await pilot.pause(0.2)
            tasks = app.screen.query_one("#tasks-table")
            assert tasks.row_count == 1
            task_row = tasks.get_row(next(iter(tasks.rows)))
            # After adding Wave column, Status is at index 2 (was index 1)
            assert task_row[2] == "[bold red]stale[/]"

    asyncio.run(_run())
