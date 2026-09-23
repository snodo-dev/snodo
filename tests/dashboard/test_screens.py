"""Headless behavior tests for the dashboard's session, event, and wave screens.

The project fixture uses the same persisted session/task/job/audit record shapes
as the engine and existing dashboard tests; Textual is driven through run_test.
"""

import asyncio
import json
import time

from textual.app import App, ComposeResult

from snodo.dashboard.providers import DashboardDataProvider
from snodo.dashboard.screens import (
    EventsScreen,
    SessionDetailScreen,
    SessionsScreen,
    WaveDetailScreen,
)


def _project(tmp_path, monkeypatch, *, with_session=True, halted=False):
    snodo = tmp_path / ".snodo"
    (snodo / "tasks" / "task_alpha").mkdir(parents=True)
    (snodo / "jobs" / "job_alpha").mkdir(parents=True)
    (snodo / "plans" / "main").mkdir(parents=True)
    now = time.time()
    (snodo / "tasks" / "task_alpha" / "state.json").write_text(json.dumps({
        "task_id": "task_alpha", "description": "Build the feature",
        "status": "completed", "started_at": now - 90,
        "completed_at": now - 20, "usage": [],
    }))
    (snodo / "jobs" / "job_alpha" / "task.json").write_text(
        json.dumps({"task_id": "task_alpha"})
    )
    (snodo / "jobs" / "job_alpha" / "state.json").write_text(json.dumps({
        "status": "completed", "created_at": now - 90,
        "started_at": now - 80, "completed_at": now - 20,
        "exit_code": 0,
    }))
    (snodo / "plans" / "main" / "status.json").write_text(json.dumps({
        "tasks": {"task_alpha": {
            "status": "completed", "parent_task_ref": None, "depth": 0,
        }},
    }))
    (snodo / "wave.json").write_text(json.dumps([{
        "wave_id": "wave_1", "feature_description": "Build the feature",
        "anchor_summaries": ["A visible intent"], "task_ids": ["task_alpha"],
        "created": now - 120, "last_activity": now - 20,
    }]))
    monkeypatch.setenv("SNODO_HOME", str(tmp_path / "home"))
    session_id = None
    if with_session:
        from snodo.infrastructure.session import SessionManager

        manager = SessionManager()
        session = manager.create_session("producer", str(tmp_path))
        manager.set_current_task(session.session_id, "task_alpha")
        session_id = session.session_id
        (snodo / "state.json").write_text(json.dumps({
            "current_mode": "producer",
            "active_session": {"producer": session_id},
            "metadata": {},
        }))
    from snodo.infrastructure.audit import AuditLog

    audit = AuditLog(str(snodo / "audit.log"), project_id="p_test")
    audit.append_event("session_started", {
        "session_id": session_id or "session-record", "mode": "producer",
    })
    audit.append_event("dispatch", {
        "session_id": session_id or "session-record", "task_ref": "task_alpha",
    })
    audit.append_event("task_complete", {
        "session_id": session_id or "session-record", "task_ref": "task_alpha",
    })
    if halted:
        audit.append_event("halt", {
            "session_id": session_id or "session-record",
            "task_ref": "task_alpha", "reason": "operator review required",
        })
    return tmp_path, session_id


class _ScreenHost(App):
    def __init__(self, screen):
        super().__init__()
        self.initial_screen = screen

    def compose(self) -> ComposeResult:
        return
        yield  # pragma: no cover - makes this a generator for Textual

    def on_mount(self):
        self.push_screen(self.initial_screen)


def _plain(widget):
    render = widget.render()
    return render.plain if hasattr(render, "plain") else str(render)


def test_sessions_screen_empty_project_is_an_empty_view(tmp_path, monkeypatch):
    project, _ = _project(tmp_path, monkeypatch, with_session=False)
    screen = SessionsScreen(DashboardDataProvider(str(project)))

    async def run():
        app = _ScreenHost(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = screen.query_one("#session-table")
            assert table.row_count == 0
            assert "sessions: 0" in _plain(screen.query_one("#session-header"))
            assert "Row 0/0" in app.sub_title

    asyncio.run(run())


def test_sessions_enter_and_escape_navigate_to_recorded_session_detail(tmp_path, monkeypatch):
    project, session_id = _project(tmp_path, monkeypatch)
    screen = SessionsScreen(DashboardDataProvider(str(project)))

    async def run():
        app = _ScreenHost(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = screen.query_one("#session-table")
            assert table.row_count == 1
            assert session_id[-12:] in str(table.get_row_at(0)[0])
            assert "active" in str(table.get_row_at(0)[5])
            await pilot.press("enter")
            await pilot.pause()
            detail = app.screen
            assert isinstance(detail, SessionDetailScreen)
            assert "producer" in _plain(detail.query_one("#detail-header"))
            assert "task_alpha" in _plain(detail.query_one("#detail-header"))
            assert detail.query_one("#events-table").row_count == 3
            assert detail.query_one("#task-history-table").row_count == 1
            screen.provider._audit_log.append_event("halt", {
                "session_id": session_id, "task_ref": "task_alpha",
                "reason": "newly halted",
            })
            detail.action_refresh()
            await pilot.pause()
            assert detail.query_one("#events-table").row_count == 4
            assert "HALTED" in _plain(detail.query_one("#detail-header"))
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is screen

    asyncio.run(run())


def test_halted_session_is_called_out_in_list_and_detail(tmp_path, monkeypatch):
    project, _ = _project(tmp_path, monkeypatch, halted=True)
    screen = SessionsScreen(DashboardDataProvider(str(project)))

    async def run():
        app = _ScreenHost(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert "halted" in str(screen.query_one("#session-table").get_row_at(0)[5])
            await pilot.press("enter")
            await pilot.pause()
            assert "HALTED" in _plain(app.screen.query_one("#detail-header"))

    asyncio.run(run())


def test_session_removed_between_list_and_open_fails_loudly(tmp_path, monkeypatch):
    project, session_id = _project(tmp_path, monkeypatch)
    screen = SessionsScreen(DashboardDataProvider(str(project)))

    async def run():
        app = _ScreenHost(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            from snodo.infrastructure.session import SessionManager

            SessionManager().delete_session(session_id)
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is screen

    asyncio.run(run())


def test_refresh_removes_a_session_that_disappeared_from_disk(tmp_path, monkeypatch):
    project, session_id = _project(tmp_path, monkeypatch)
    screen = SessionsScreen(DashboardDataProvider(str(project)))

    async def run():
        app = _ScreenHost(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert screen.query_one("#session-table").row_count == 1
            from snodo.infrastructure.session import SessionManager

            SessionManager().delete_session(session_id)
            screen.action_refresh()
            assert screen.query_one("#session-table").row_count == 0
            assert session_id not in screen._row_keys

    asyncio.run(run())


def test_sessions_filter_and_clear_restore_rows(tmp_path, monkeypatch):
    project, session_id = _project(tmp_path, monkeypatch)
    screen = SessionsScreen(DashboardDataProvider(str(project)))

    async def run():
        app = _ScreenHost(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen.action_filter_mode()
            screen.query_one("#filter-bar").value = "does-not-match"
            screen._apply_filter("does-not-match")
            table = screen.query_one("#session-table")
            assert table.row_count == 0
            screen.action_clear_filter()
            assert table.row_count == 1
            assert session_id in screen._row_keys

    asyncio.run(run())


def test_events_screen_shows_audit_tail_and_filters_by_summary(tmp_path, monkeypatch):
    project, _ = _project(tmp_path, monkeypatch)
    screen = EventsScreen(DashboardDataProvider(str(project)))

    async def run():
        app = _ScreenHost(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = screen.query_one("#events-table")
            assert table.row_count == 3
            screen._apply_events_filter("task_alpha")
            assert table.row_count == 2
            assert {str(table.get_row_at(i)[2]) for i in range(table.row_count)} == {
                "[bold green]task_complete[/]", "[green]dispatch[/]",
            }
            screen._apply_events_filter("")
            assert table.row_count == 3

    asyncio.run(run())


def test_wave_detail_missing_task_and_empty_data_are_operator_readable():
    screen = WaveDetailScreen(
        {"wave_id": "wave_missing", "task_ids": ["removed_task"]},
        {},
    )

    async def run():
        app = _ScreenHost(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert "wave_missing" in _plain(screen.query_one("#wave-header"))
            assert "(no description)" in _plain(screen.query_one(".wave-description"))
            table = list(screen.query("DataTable"))[0]
            row = table.get_row_at(0)
            assert str(row[0]).endswith("removed_task")
            assert row[1] == "?"
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is not screen

    asyncio.run(run())
