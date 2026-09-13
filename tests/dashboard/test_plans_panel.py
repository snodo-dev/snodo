"""The plans panel and status-coloured rows.

FILE: tests/dashboard/test_plans_panel.py

Two things the cockpit did not say while a real plan ran: that a set of jobs
belonged to one plan (they read as unrelated rows, and the connection had to
be inferred from task names), and what a table of rows said at a glance (each
status was a word to be read one row at a time).

These tests pin what a viewer would check:
- a plan with several waves renders its tasks under the wave they belong to,
  with the statuses status.json records, and shows the waves' dependencies;
- a task that is running now names the job carrying it;
- a job belonging to a plan is shown as belonging to it where it is listed;
- a plan with no status.json yet renders, pending, without raising;
- a row's style follows its status for every state in the mapping — and the
  mapping keeps failure distinct from the reserved red of a human-waiting row;
- the colour never travels alone: the status word stays in its column;
- a selected row survives a refresh that clears and rebuilds the table (#239);
- the panel observes: it changes nothing on disk and offers no action.
"""

import json
import os
import time
from typing import Any, Dict, List

from snodo.dashboard.app import SnodoDashboard
from snodo.dashboard.panels import get_panel
from snodo.dashboard.panels.cockpit import CockpitScreen
from snodo.dashboard.screens import _STATUS_ROW_STYLES, status_row_style


# ---------------------------------------------------------------------------
# Fixture: a project with a two-wave plan and the work a wave has spawned
# ---------------------------------------------------------------------------

_PLAN_WAVES = [
    {"id": 1, "depends_on": [], "tasks": ["task_a", "task_b"]},
    {"id": 2, "depends_on": [1], "tasks": ["task_c"]},
]

_PLAN_STATUSES = {
    "task_a": {"status": "completed", "parent_task_ref": None, "depth": 0},
    "task_b": {"status": "in_progress", "parent_task_ref": None, "depth": 0},
    "task_c": {"status": "pending", "parent_task_ref": None, "depth": 0},
}


def _write_tree(snodo: Any) -> None:
    (snodo / "tasks").mkdir(parents=True, exist_ok=True)
    (snodo / "jobs").mkdir(parents=True, exist_ok=True)
    (snodo / "plans" / "orch").mkdir(parents=True, exist_ok=True)


def _plan_project(
    tmp_path, monkeypatch,
    *,
    with_status: bool = True,
    plan_name: str = "orch",
    statuses: Dict[str, str] | None = None,
) -> Any:
    """A session, plan 'orch' (two waves), and the job carrying task_b.

    ``statuses`` replaces the status.json task map entirely (keys must be the
    plan's task ids or a subset of them) for tests that need other states.
    """
    snodo = tmp_path / ".snodo"
    _write_tree(snodo)
    now = time.time()
    plan_dir = snodo / "plans" / plan_name
    plan_dir.mkdir(parents=True, exist_ok=True)

    (plan_dir / "plan.yml").write_text(json.dumps({
        "name": plan_name, "intent": "show the plan", "waves": _PLAN_WAVES,
    }))
    if with_status:
        if statuses is not None:
            tasks = {
                tid: {"status": statuses[tid], "parent_task_ref": None, "depth": 0}
                for tid in statuses
            }
        else:
            tasks = _PLAN_STATUSES
        (plan_dir / "status.json").write_text(json.dumps({"tasks": tasks}))

    (snodo / "wave.json").write_text(json.dumps([]))

    job_dir = snodo / "jobs" / "j_b"
    job_dir.mkdir(parents=True)
    (job_dir / "task.json").write_text(json.dumps({"task_id": "task_b"}))
    (job_dir / "state.json").write_text(json.dumps({
        "status": "running", "pid": os.getpid(),
        "created_at": now - 60, "started_at": now - 60, "usage": [],
    }))

    monkeypatch.setenv("SNODO_HOME", str(tmp_path / "home"))
    from snodo.infrastructure.session import SessionManager
    mgr = SessionManager()
    sess = mgr.create_session("producer", str(tmp_path))
    mgr.set_current_task(sess.session_id, "task_b")
    (snodo / "state.json").write_text(json.dumps({
        "current_mode": "producer",
        "active_session": {"producer": sess.session_id},
        "metadata": {},
    }))
    return tmp_path


def _table_grid(table) -> List[List[str]]:
    return [[str(c) for c in table.get_row_at(i)] for i in range(table.row_count)]


def _open_plans(app, project):
    """Push the plans panel onto a running app and return the screen."""
    from snodo.dashboard.providers import DashboardDataProvider
    screen = get_panel("plans", DashboardDataProvider(str(project)))
    app.push_screen(screen)
    return screen


# ---------------------------------------------------------------------------
# 1. The plan panel: waves, membership, dependencies, recorded statuses
# ---------------------------------------------------------------------------


def test_plan_panel_renders_waves_tasks_and_statuses(tmp_path, monkeypatch):
    import asyncio

    project = _plan_project(tmp_path, monkeypatch)

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = _open_plans(app, project)
            await pilot.pause(0.3)
            table = screen.query_one("#plans-table")
            grid = _table_grid(table)
            rows = [(cells[0], cells[1]) for cells in grid]

            # Wave rows sit between the plan row and the task rows, and each
            # task sits under the wave plan.yml assigns it to.
            keys = [r[0] for r in rows]
            wave1 = next(i for i, k in enumerate(keys) if "Wave 1" in k)
            wave2 = next(i for i, k in enumerate(keys) if "Wave 2" in k)
            row_of = {k.strip(): (i, s) for i, (k, s) in enumerate(rows)}
            assert wave1 < row_of["↳ task_a"][0] < wave2  # under wave 1
            assert wave1 < row_of["↳ task_b"][0] < wave2
            assert wave2 < row_of["↳ task_c"][0]  # under wave 2

            # Statuses as status.json records them — the word stays visible.
            assert row_of["↳ task_a"][1] == "completed"
            assert row_of["↳ task_b"][1] == "in_progress"
            assert row_of["↳ task_c"][1] == "pending"

            # The dependency plan.yml declares is shown on the wave row.
            wave_row = next(cells for cells in grid if "Wave 2" in cells[0])
            assert "after: 1" in wave_row[3]

    asyncio.run(_run())


def test_plan_panel_names_the_job_carrying_a_running_task(tmp_path, monkeypatch):
    import asyncio

    project = _plan_project(tmp_path, monkeypatch)

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = _open_plans(app, project)
            await pilot.pause(0.3)
            table = screen.query_one("#plans-table")
            grid = {cells[0].strip(): cells for cells in _table_grid(table)}
            # The running task names its carrier, so the job log is one step
            # away (the Jobs pane, one search away); settled tasks name none.
            assert grid["↳ task_b"][2] == "j_b"
            assert grid["↳ task_a"][2] == "—"

    asyncio.run(_run())


def test_plan_panel_renders_plan_without_status_json(tmp_path, monkeypatch):
    """A plan proposed but not yet run: its waves show, tasks read pending."""
    import asyncio

    project = _plan_project(tmp_path, monkeypatch, with_status=False)

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = _open_plans(app, project)
            await pilot.pause(0.3)
            table = screen.query_one("#plans-table")
            grid = {cells[0].strip(): cells for cells in _table_grid(table)}
            assert grid["↳ task_a"][1] == "pending"
            assert grid["↳ task_c"][1] == "pending"

    asyncio.run(_run())


def test_plan_panel_observes_and_changes_nothing(tmp_path, monkeypatch):
    """Rendering the panel is pure reading: no file written, no action bound."""
    import asyncio

    project = _plan_project(tmp_path, monkeypatch)

    def _snapshot() -> Dict[str, Any]:
        out = {}
        for dirpath, _, files in os.walk(project / ".snodo"):
            for name in files:
                p = os.path.join(dirpath, name)
                out[p] = (os.path.getmtime(p), open(p, "rb").read())
        return out

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            before = _snapshot()
            _open_plans(app, project)
            await pilot.pause(0.3)
            await pilot.press("r")
            await pilot.pause(0.2)
            after = _snapshot()
            assert before == after

    asyncio.run(_run())

    # No run/retry/cancel anywhere: the only action the panel itself defines
    # is the refresh that re-reads, and the keys bound are just that, back,
    # and quit. (Screen inherits generic focus/scroll actions; those are not
    # the dashboard acting on state.)
    from snodo.dashboard.panels.plans import PlansScreen
    own_actions = {
        n for n in vars(PlansScreen)
        if n.startswith("action_")
    }
    assert own_actions == {"action_refresh"}, own_actions
    assert {b.key for b in PlansScreen.BINDINGS} == {"q", "r", "escape"}


# ---------------------------------------------------------------------------
# 2. A job belonging to a plan says so where it is listed
# ---------------------------------------------------------------------------


def test_cockpit_job_row_names_its_plan(tmp_path, monkeypatch):
    import asyncio

    project = _plan_project(tmp_path, monkeypatch)
    # A second job whose task no plan claims, for the contrast.
    job_dir = project / ".snodo" / "jobs" / "j_free"
    job_dir.mkdir(parents=True)
    (job_dir / "task.json").write_text(json.dumps({"task_id": "task_unplanned"}))
    (job_dir / "state.json").write_text(json.dumps({
        "status": "completed", "pid": None, "created_at": 5.0,
        "started_at": 4.0, "completed_at": 6.0, "usage": [],
    }))

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CockpitScreen)
            await pilot.pause(0.3)
            jobs = screen.query_one("#jobs-table")
            tasks_by_job = {
                str(jobs.get_row(rk)[0]): str(jobs.get_row(rk)[1])
                for rk in jobs.rows
            }
            # The planned job names its plan in the shape the Tasks pane keys
            # by; the unplanned job stays exactly as recorded.
            assert tasks_by_job["j_b"] == "orch:task_b"
            assert tasks_by_job["j_free"] == "task_unplanned"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 3. Rows wear their status
# ---------------------------------------------------------------------------


def test_status_row_style_covers_the_vocabulary():
    """Each decided status has its style; failure orange is not the red of a
    human-waiting row; unlisted statuses fall back to plain, never raising."""
    assert status_row_style("running") == "green"
    assert status_row_style("in_progress") == "green"
    assert status_row_style("completed") == "dim"
    assert status_row_style("merged") == "dim"
    assert status_row_style("cancelled") == "dim italic"
    assert status_row_style("unmerged") == "yellow"
    assert status_row_style("pending") == ""
    assert status_row_style("queued") == ""

    failed = status_row_style("failed")
    assert "color(208)" in failed  # orange, the terminal's orange
    assert "red" not in failed and "red" not in status_row_style("errored")
    for waiting in ("blocked", "escalated", "halted", "stale"):
        assert status_row_style(waiting) == "red"

    assert status_row_style("what-is-this") == ""
    assert status_row_style(None) == ""

    # The mapping and the vocabulary the tables carry agree: every status the
    # planner or the job manager writes is decided, not defaulted.
    decided = {
        "pending", "in_progress", "completed", "blocked", "errored",
        "unmerged", "escalated",  # planner's status.json vocabulary
        "queued", "running", "cancelled", "failed",  # job state.json
        "merged", "halted", "stale",  # other words the tables show
    }
    assert decided <= set(_STATUS_ROW_STYLES)


def test_cockpit_rows_are_styled_by_their_status(tmp_path, monkeypatch):
    """A row's whole-row style follows its status, and the status word stays
    in its column (colour never travels alone)."""
    import asyncio

    statuses = {f"task_{i}": s for i, s in enumerate(
        ["completed", "in_progress", "pending", "unmerged",
         "errored", "blocked", "cancelled"]
    )}
    project = _plan_project(tmp_path, monkeypatch, plan_name="many", statuses=statuses)

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.pause(0.3)
            table = screen.query_one("#tasks-table")
            assert table.row_count >= len(statuses)
            styled = {
                str(table.get_row(rk)[0]): table._row_styles.get(rk, "")
                for rk in table.rows
            }
            for tid, status in statuses.items():
                assert styled[tid] == status_row_style(status), tid
            # The word itself is still in the column for every row: a reader
            # without colour reads the same table.
            words = {str(table.get_row(rk)[2]) for rk in table.rows}
            assert set(statuses.values()) <= words
            # And the stored style is the one the widget resolves when it
            # renders the row — not just a note kept beside the table.
            from rich.style import Style
            green = table._get_row_style(
                table.get_row_index("many:task_1"), Style()
            )
            assert green.color is not None and green.color.name == "green"

    asyncio.run(_run())


def test_cockpit_stale_task_row_is_red_not_green(tmp_path, monkeypatch):
    """Liveness corrects the colour too: a record claiming to run with no
    sign of life is a person-needed red, not the green of live work."""
    import asyncio

    project = _plan_project(tmp_path, monkeypatch)
    (project / ".snodo" / "tasks" / "task_b").mkdir(parents=True)
    (project / ".snodo" / "tasks" / "task_b" / "state.json").write_text(json.dumps({
        "task_id": "task_b", "status": "running",
        "started_at": time.time() - 3 * 86400, "pid": 999_999_999, "usage": [],
    }))

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.pause(0.3)
            table = screen.query_one("#tasks-table")
            key = table.rows["orch:task_b"].key
            assert table._row_styles.get(key) == "red"
            assert "stale" in str(table.get_row(key)[2])

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 4. Selection survives the rebuild
# ---------------------------------------------------------------------------


def test_cockpit_selection_survives_refresh(tmp_path, monkeypatch):
    """After arrowing to a row and refreshing (clear + rebuild), the cursor is
    still on the same row and the cascade selection is untouched (#239)."""
    import asyncio

    project = _plan_project(tmp_path, monkeypatch)

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CockpitScreen)
            await pilot.pause(0.3)
            table = screen.query_one("#tasks-table")

            await pilot.press("down")  # operator move: task_a → task_b row
            await pilot.pause(0.2)
            key = table.key_at_row(table.cursor_row)
            selected = screen.selected_task
            assert key is not None and selected is not None

            await pilot.press("r")
            await pilot.pause(0.3)

            assert screen.selected_task == selected
            assert table.key_at_row(table.cursor_row) == key
            assert screen._programmatic_move is False

    asyncio.run(_run())


def test_plans_panel_selection_survives_refresh(tmp_path, monkeypatch):
    import asyncio

    project = _plan_project(tmp_path, monkeypatch)

    async def _run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = _open_plans(app, project)
            await pilot.pause(0.3)
            table = screen.query_one("#plans-table")

            await pilot.press("down")
            await pilot.press("down")
            await pilot.pause(0.2)
            key = table.key_at_row(table.cursor_row)
            assert key is not None

            await pilot.press("r")
            await pilot.pause(0.3)
            assert table.key_at_row(table.cursor_row) == key

    asyncio.run(_run())
