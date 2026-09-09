"""Tests for the cockpit's reclaimed layout: the Needs You pane and search.

FILE: tests/dashboard/test_cockpit_attention.py

The cockpit was spending half its height on a Sessions pane whose only fact —
which session is active — is already printed in the header. That space is
returned to what requires a human: a task that escalated, or that halted on a
judge which could not reach a verdict, is the only thing on the screen that
will not progress unless a person acts. The operator used to have to remember
to run `snodo authorize` to learn such a task existed.

These tests pin:
- the header carries the active session and the Sessions pane is gone;
- a task awaiting a human appears with what it awaits and how long it has
  waited; when nothing awaits, the pane says so plainly;
- one search finds a task, a job and an audit event and moves the operator to
  the match, and it performs no read whose cost grows with a job's output;
- the screen still performs exactly one read on mount and one per explicit
  refresh — no timer, no read as the operator moves inside a settled pane.

The dashboard remains an observer: no action here writes or locks anything.
"""

import json
import os
import time
from datetime import datetime, UTC

from snodo.dashboard.app import SnodoDashboard
from snodo.dashboard.panels.cockpit import CockpitScreen
from snodo.dashboard.screens import _short_id


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _audit_line(seq, event_type, task_ref, ts, **data):
    return json.dumps({
        "sequence": seq,
        "timestamp": datetime.fromtimestamp(ts, UTC).isoformat(),
        "event_type": event_type,
        "project_id": "p_test",
        "data": {"op": event_type, "task_ref": task_ref, **data},
        "previous_hash": "0" * 64,
        "event_hash": "1" * 64,
    })


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
    (d / "task.json").write_text(json.dumps({"task_id": task_id}))


def _attention_fixture(tmp_path, monkeypatch, *, awaiting=True):
    """A session, one task, one job, an audit tail.

    With ``awaiting=True`` the tail records an escalated task and a task that
    halted on an abstaining judge — the two things that will not move without a
    person. The escalate event is deliberately old so 'how long' is visible.
    """
    snodo = tmp_path / ".snodo"
    (snodo / "tasks").mkdir(parents=True)
    (snodo / "jobs").mkdir(parents=True)
    (snodo / "plans" / "main").mkdir(parents=True)
    now = time.time()

    lines = [
        _audit_line(0, "validate", "task_alpha", now - 800, phase="pre_execute", outcome="passed"),
        _audit_line(1, "dispatch", "task_alpha", now - 600, coder="litellm"),
    ]
    if awaiting:
        lines += [
            _audit_line(2, "disagreement_escalated", "task_needs_human", now - 7200,
                        phase="post_execute", detail="uniqueterm-escalate"),
            _audit_line(3, "halt", "task_abstained", now - 300,
                        reason="1 validator(s) abstained: could not produce verdicts within budget",
                        halt_type="blocker", final_decision="blocker"),
        ]
    _write_audit(tmp_path, lines)

    _write_task_state(tmp_path, "task_alpha", {
        "task_id": "task_alpha", "description": "add attention", "status": "running",
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
    (snodo / "wave.json").write_text(json.dumps(
        [{"wave_id": "w_0001", "feature_description": "attention", "task_ids": ["task_alpha"]}]
    ))
    (snodo / "plans" / "main" / "status.json").write_text(json.dumps({
        "tasks": {"task_alpha": {"status": "in_progress", "parent_task_ref": None, "depth": 0}}
    }))

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
    return tmp_path, sess.session_id


def _pane_plain(pane):
    return "\n".join(strip.text for strip in pane.lines)


# ---------------------------------------------------------------------------
# 1. Reclaim the Sessions pane: header carries it, upper row does not
# ---------------------------------------------------------------------------


def test_header_carries_active_session_and_sessions_pane_is_gone(tmp_path, monkeypatch):
    import asyncio

    project, session_id = _attention_fixture(tmp_path, monkeypatch)

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CockpitScreen)
            await pilot.pause(0.2)

            header = screen.query_one("#cockpit-header")
            rendered = header.render().plain if hasattr(header.render(), "plain") else str(header.render())
            assert _short_id(session_id) in rendered
            assert "Active Session" in rendered

            # The Sessions pane no longer occupies the upper row.
            assert not list(screen.query("#sessions-table"))
            screen.query_one("#attention-pane")  # raises if absent

    asyncio.run(_run())


def test_needs_you_pane_is_in_the_upper_row(tmp_path, monkeypatch):
    """The pane that replaced Sessions sits in the top row, not the bottom."""
    import asyncio

    project, _ = _attention_fixture(tmp_path, monkeypatch)

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.pause(0.2)
            rows = screen.query(".cockpit-row")
            first_row = list(rows)[0]
            ids_in_first_row = [w.id for w in first_row.query("RichLog, DataTable")]
            assert "attention-pane" in ids_in_first_row
            assert "tasks-table" in ids_in_first_row
            assert "sessions-table" not in ids_in_first_row

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 2. A task awaiting a human appears with what it awaits; empty says so
# ---------------------------------------------------------------------------


def test_awaiting_task_shown_with_what_and_how_long(tmp_path, monkeypatch):
    import asyncio

    project, _ = _attention_fixture(tmp_path, monkeypatch, awaiting=True)

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.pause(0.2)
            text = _pane_plain(screen.query_one("#attention-pane"))

            # Both waiting tasks, what each awaits, and how long it has waited.
            assert "waiting on a person" in text
            assert "task_needs_human" in text
            assert "task_abstained" in text
            assert "authorize" in text            # the escalated one
            assert "judges could not reach" in text  # the abstention one
            assert "2h" in text                    # the escalated one has waited ~2h

    asyncio.run(_run())


def test_halts_grouped_by_outcome_and_cost_aggregated(tmp_path, monkeypatch):
    """A wave failing the same way reads as one fact, and cost is totalled."""
    import asyncio

    project, _ = _attention_fixture(tmp_path, monkeypatch, awaiting=True)

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.pause(0.2)
            text = _pane_plain(screen.query_one("#attention-pane"))
            assert "Halts by outcome" in text
            # escalate×1, abstention×1, blocker×1 grouped as outcome counts.
            assert "escalate" in text and "abstention" in text
            assert "Cost" in text
            # task (0.03) and its wrapping job (0.10) are not summed to 0.13.
            assert "0.0300" in text

    asyncio.run(_run())


def test_nothing_waiting_says_so_plainly(tmp_path, monkeypatch):
    import asyncio

    project, _ = _attention_fixture(tmp_path, monkeypatch, awaiting=False)

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.pause(0.2)
            text = _pane_plain(screen.query_one("#attention-pane"))
            assert "Nothing is waiting on you." in text

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 3. One search finds a task, a job and an audit event, and moves the operator
# ---------------------------------------------------------------------------


def test_search_finds_task_and_moves_cursor(tmp_path, monkeypatch):
    import asyncio

    project, _ = _attention_fixture(tmp_path, monkeypatch)

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.pause(0.2)
            hits = screen.provider.search("task_alpha")
            assert any(h["kind"] == "task" for h in hits)
            screen._run_search("task_alpha")
            await pilot.pause()
            assert screen.selected_task == "main:task_alpha"
            tasks = screen.query_one("#tasks-table")
            assert tasks.has_focus

    asyncio.run(_run())


def test_search_finds_job_and_shows_its_log(tmp_path, monkeypatch):
    import asyncio

    project, _ = _attention_fixture(tmp_path, monkeypatch)

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.pause(0.2)
            hits = screen.provider.search("j_alpha")
            assert any(h["kind"] == "job" for h in hits)
            screen._run_search("j_alpha")
            assert screen.selected_job == "j_alpha"
            text = _pane_plain(screen.query_one("#log-pane"))
            assert "building" in text

    asyncio.run(_run())


def test_search_finds_audit_event_from_the_tail(tmp_path, monkeypatch):
    import asyncio

    project, _ = _attention_fixture(tmp_path, monkeypatch, awaiting=True)

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.pause(0.2)
            hits = screen.provider.search("uniqueterm-escalate")
            assert any(h["kind"] == "audit" for h in hits)
            screen._run_search("uniqueterm-escalate")
            text = _pane_plain(screen.query_one("#log-pane"))
            assert "disagreement_escalated" in text

    asyncio.run(_run())


def test_search_performs_no_read_that_grows_with_job_output(tmp_path, monkeypatch):
    """Searching must never read a job's stdout/stderr: that is where the
    megabytes are, and it would restore the cost the bounded tail removed.
    (Selecting a found job to view its bounded log tail is a separate, already
    bounded step — this test covers the search operation itself.)"""
    import asyncio
    from unittest.mock import patch

    project, _ = _attention_fixture(tmp_path, monkeypatch)

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.pause(0.2)

            def _boom(*a, **k):
                raise AssertionError("search must not read a job's log or the whole audit chain")

            with patch.object(screen.provider, "get_job_log", side_effect=_boom), \
                 patch("snodo.dashboard.providers.AuditLog", side_effect=_boom):
                # Tasks, jobs and audit events are all found without either read.
                assert screen.provider.search("task_alpha")
                assert screen.provider.search("j_alpha")
                assert screen.provider.search("validate")

    asyncio.run(_run())


def test_default_search_excludes_output_but_explicit_search_reads_bounded_tail(tmp_path, monkeypatch):
    """Output search exists only as an explicit request and reads a tail."""
    from snodo.dashboard.providers import DashboardDataProvider

    project, _ = _attention_fixture(tmp_path, monkeypatch)
    big = project / ".snodo" / "jobs" / "j_alpha" / "stdout.log"
    # "needle" is in the tail; a marker at the head is beyond the bounded read.
    big.write_text("HEADMARKER\n" + ("x" * 200 + "\n") * 20000 + "needle\n")  # > 3MB

    provider = DashboardDataProvider(str(project))
    assert provider.search("needle") == []       # default never touches output
    assert provider.search("HEADMARKER") == []

    # The explicit, opt-in path finds the tail marker — and stays bounded: the
    # head of a >3MB file is never read.
    import snodo.dashboard.providers as prov

    real_read = prov._read_tail_text

    def _counting_read(path, max_bytes=prov._LOG_TAIL_BYTES):
        assert path.name in ("stdout.log", "stderr.log")
        assert max_bytes == prov._LOG_TAIL_BYTES  # bounded, never whole file
        text = real_read(path, max_bytes)
        assert "HEADMARKER" not in text            # cost did not grow with size
        return text

    monkeypatch.setattr(prov, "_read_tail_text", _counting_read)
    hits = provider.search_job_output("needle")
    assert hits and hits[0]["key"] == "j_alpha"


# ---------------------------------------------------------------------------
# 4. Exactly one read on mount and one per explicit refresh
# ---------------------------------------------------------------------------


def test_one_read_on_mount_and_one_per_explicit_refresh(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import patch

    import snodo.dashboard.providers as prov

    project, _ = _attention_fixture(tmp_path, monkeypatch)

    calls = {"n": 0}
    real = prov.collect_snapshot

    def _counting_snapshot(project_root, now=None):
        calls["n"] += 1
        return real(project_root, now)

    async def _run():
        with patch.object(prov, "collect_snapshot", _counting_snapshot):
            app = SnodoDashboard(project_root=str(project))
            async with app.run_test() as pilot:
                await pilot.pause()
                screen = app.screen
                assert isinstance(screen, CockpitScreen)
                await pilot.pause(0.3)
                after_mount = calls["n"]
                assert after_mount == 1, "mount must perform exactly one read pass"

                # An explicit refresh performs exactly one more read pass.
                screen.action_refresh()
                await pilot.pause(0.2)
                assert calls["n"] == after_mount + 1

                # Moving inside a settled pane performs no read pass.
                before_move = calls["n"]
                screen._select_in_table("#tasks-table", "main:task_alpha")
                await pilot.pause(0.1)
                assert calls["n"] == before_move

    asyncio.run(_run())


def test_no_timer_is_scheduled(tmp_path, monkeypatch):
    """Refresh stays explicit: the screen sets no repeating timer."""
    import asyncio
    from unittest.mock import patch

    project, _ = _attention_fixture(tmp_path, monkeypatch)

    async def _run():
        with patch.object(CockpitScreen, "set_timer", side_effect=AssertionError(
            "the cockpit must not schedule a timer; refresh is explicit"
        )) as st:
            app = SnodoDashboard(project_root=str(project))
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.pause(0.3)
                st.assert_not_called()

    asyncio.run(_run())
