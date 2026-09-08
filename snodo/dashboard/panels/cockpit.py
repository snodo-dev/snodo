"""Linked Cascade Cockpit panel screen for Snodo dashboard.

FILE: snodo/dashboard/panels/cockpit.py
"""

from contextlib import suppress
from typing import Any, Dict, Optional, List

from rich.markup import escape as _escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, RichLog, Static
from textual.widgets.data_table import RowDoesNotExist

from snodo.dashboard.panels import register_panel, get_panel
from snodo.dashboard.screens import _short_id


def _flatten_tasks(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten tasks into recovery hierarchical list."""
    by_parent: Dict[str, List[Dict[str, Any]]] = {}
    roots: List[Dict[str, Any]] = []
    for t in tasks:
        parent = t.get("parent_task_ref")
        if not parent:
            roots.append(t)
        else:
            if parent not in by_parent:
                by_parent[parent] = []
            by_parent[parent].append(t)

    flat: List[Dict[str, Any]] = []

    def traverse(t):
        flat.append(t)
        ref = t["task_ref"]
        tid = t["task_id"]
        children = by_parent.get(ref, []) + by_parent.get(tid, [])
        # Deduplicate children
        seen = set()
        deduped = []
        for child in children:
            if child["task_ref"] not in seen:
                seen.add(child["task_ref"])
                deduped.append(child)
        for child in deduped:
            traverse(child)

    for r in roots:
        traverse(r)

    # Orphaned fallback
    seen_refs = {t["task_ref"] for t in flat}
    for t in tasks:
        if t["task_ref"] not in seen_refs:
            flat.append(t)

    return flat


@register_panel("cockpit")
class CockpitScreen(Screen):
    """Cockpit view: sessions | waves | tasks top row, jobs | logs bottom row."""

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding(":", "command_mode", "Command"),
    ]

    CSS = """
    CockpitScreen {
        layout: vertical;
    }
    #cockpit-header {
        height: auto;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
        border-bottom: solid $primary;
    }
    .pane-title {
        background: $primary;
        color: $text;
        text-align: center;
        text-style: bold;
        height: 1;
    }
    .cockpit-row {
        height: 1fr;
    }
    .pane {
        width: 1fr;
        border: tall $surface-lighten-1;
        margin: 0 1;
    }
    DataTable {
        height: 1fr;
    }
    RichLog {
        height: 1fr;
        background: $surface;
    }
    #command-bar {
        height: 1;
        dock: bottom;
        visibility: hidden;
        border-top: solid $primary;
    }
    #command-bar:focus-within {
        visibility: visible;
    }
    """

    def __init__(self, provider: Any, **kwargs):
        super().__init__(**kwargs)
        self.provider = provider
        self._refresh_timer: Any = None
        self._programmatic_move = False  # Flag to distinguish refresh moves from keypresses

        # Selection state cache
        self.selected_session: Optional[str] = None
        self.selected_task: Optional[str] = None
        self.selected_job: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="cockpit-header")

        # Top row: Sessions | Tasks Tree
        with Horizontal(classes="cockpit-row"):
            with Vertical(classes="pane"):
                yield Static("Sessions", classes="pane-title")
                yield DataTable(id="sessions-table", cursor_type="row")
            with Vertical(classes="pane"):
                yield Static("Tasks Tree", classes="pane-title")
                yield DataTable(id="tasks-table", cursor_type="row")

        # Bottom row: Jobs | Live Log
        with Horizontal(classes="cockpit-row"):
            with Vertical(classes="pane"):
                yield Static("Jobs", classes="pane-title")
                yield DataTable(id="jobs-table", cursor_type="row")
            with Vertical(classes="pane"):
                yield Static("Live Log", classes="pane-title")
                yield RichLog(id="log-pane", highlight=True, markup=True)

        yield Input(id="command-bar", placeholder=":command  (e.g. :protocol, :settings, :sessions)")
        yield Footer()

    def on_mount(self):
        # Configure columns
        sessions_table = self.query_one("#sessions-table", DataTable)
        sessions_table.add_columns("Session", "Mode", "Status")

        tasks_table = self.query_one("#tasks-table", DataTable)
        tasks_table.add_columns("Task ID", "Wave", "Status", "Phase", "Phase For", "Idle", "Alive?", "Cost")

        jobs_table = self.query_one("#jobs-table", DataTable)
        jobs_table.add_columns("Job ID", "Status", "Phase", "Phase For", "Idle", "Alive?", "Cost")

        # Populate initial data
        self._refresh()
        self._refresh_timer = self.set_interval(2.0, self._refresh)

    def on_screen_resume(self):
        self._refresh()

    def action_refresh(self):
        self._refresh()

    def action_command_mode(self):
        cmd = self.query_one("#command-bar", Input)
        cmd.visible = True
        cmd.focus()

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "command-bar":
            raw = event.value.strip()
            event.input.value = ""
            event.input.visible = False
            self.query_one(DataTable).focus()
            if raw.startswith(":"):
                cmd = raw[1:].strip().lower()
                self._handle_command(cmd)

    def _handle_command(self, cmd: str):
        known = {"sessions", "protocol", "settings"}
        if cmd in known:
            self.app.push_screen(get_panel(cmd, self.provider))
        elif cmd in ("cockpit", "dashboard"):
            self.notify("Already in cockpit view")
        else:
            self.notify(f"Unknown command: :{cmd}", severity="error")

    def _refresh(self):
        """Standard refresh to query provider and update pane hierarchies.

        Uses _programmatic_move flag to prevent RowHighlighted events from
        being mistaken for user keypresses when tables are cleared and rebuilt.
        """
        # Mark this as a programmatic update (not user input)
        self._programmatic_move = True

        try:
            # 1. Update Sessions
            sessions = self.provider.get_sessions()
            sessions_table = self.query_one("#sessions-table", DataTable)

            # Save cursor position or select first session if none selected
            current_sel_session = self.selected_session

            sessions_table.clear()
            for s in sessions:
                status_str = "active" if s.is_active else "—"
                if s.is_escalated:
                    status_str = "esc"
                elif s.is_halted:
                    status_str = "halted"
                sessions_table.add_row(_short_id(s.session_id), s.mode, status_str, key=s.session_id)

            if not current_sel_session and sessions:
                current_sel_session = sessions[0].session_id

            self.selected_session = current_sel_session
            if self.selected_session:
                # Restore cursor position without triggering event handlers
                with suppress(RowDoesNotExist):
                    sessions_table.move_cursor(row=sessions_table.get_row_index(self.selected_session))

            # Update Cockpit Header
            self._update_header()

            # Trigger cascade update (which will also use _programmatic_move)
            self._cascade_update()
        finally:
            # Always clear the flag when done, even if an exception occurred
            self._programmatic_move = False

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Handle user moving cursor to update cascade state.

        Ignores programmatic cursor moves from refresh to avoid resetting selection
        when tables are cleared and rebuilt (#239).
        """
        # Ignore programmatic cursor moves during refresh
        if self._programmatic_move:
            return

        table_id = event.data_table.id
        row_key = event.row_key
        if not row_key:
            return
        row_id = getattr(row_key, "value", None) or str(row_key)

        if table_id == "sessions-table":
            if self.selected_session != row_id:
                self.selected_session = row_id
                self.selected_task = None
                self.selected_job = None
                self._cascade_update()
        elif table_id == "tasks-table":
            if self.selected_task != row_id:
                self.selected_task = row_id
                self.selected_job = None
                self._cascade_update()
        elif table_id == "jobs-table":
            if self.selected_job != row_id:
                self.selected_job = row_id
                self._cascade_update()

    def _cascade_update(self):
        """Update tables in cascade based on selection states.

        Hierarchy: Session → Tasks (with wave shown as a column) → Jobs → Live Log.
        Waves are no longer a filter level; all tasks from a session are shown.
        """
        session_id = self.selected_session
        if not session_id:
            self.query_one("#tasks-table", DataTable).clear()
            self.query_one("#jobs-table", DataTable).clear()
            self._update_live_log(None, None, None)
            return

        # Build a wave_id lookup map: task_ref → wave_id
        waves = self.provider.get_waves(session_id)
        task_to_wave: Dict[str, str] = {}
        for w in waves:
            for task_id in w.get("task_ids", []):
                # Store mapping for both task_id and task_ref (which might be same)
                task_to_wave[task_id] = w["wave_id"]

        # Update Tasks - show all tasks from session, no wave filtering
        tasks_table = self.query_one("#tasks-table", DataTable)
        self._programmatic_move = True
        tasks_table.clear()
        self._programmatic_move = False

        all_tasks = self.provider.get_tasks(session_id)
        flat_tasks = _flatten_tasks(all_tasks)

        for t in flat_tasks:
            indent = "  " * t["depth"] + ("↳ " if t["depth"] > 0 else "")
            display_id = indent + t["task_id"]
            wave_id = task_to_wave.get(t["task_id"], t.get("wave_id", "—"))
            status = t["status"]
            liveness = self.provider.get_task_liveness(t["task_id"])
            if liveness is not None:
                if self.provider.is_stale_row(liveness):
                    status = "[bold red]stale[/]"
                elif liveness.is_terminal():
                    status = f"[dim]{status}[/dim]"
                cells = [display_id, wave_id, status] + self.provider.liveness_cells(liveness)
            else:
                cells = [display_id, wave_id, status, "—", "—", "—", "—", "—"]
            tasks_table.add_row(*cells, key=t["task_ref"])

        # Restore cursor position for tasks
        if self.selected_task and flat_tasks:
            with suppress(RowDoesNotExist):
                self._programmatic_move = True
                tasks_table.move_cursor(row=tasks_table.get_row_index(self.selected_task))
                self._programmatic_move = False
        elif flat_tasks:
            self.selected_task = flat_tasks[0]["task_ref"]

        # Update Jobs
        task_ref = self.selected_task
        jobs_table = self.query_one("#jobs-table", DataTable)
        self._programmatic_move = True
        jobs_table.clear()
        self._programmatic_move = False

        if task_ref:
            jobs = self.provider.get_jobs(session_id, task_ref)
            for j in jobs:
                job_id = j["job_id"]
                status = j["status"]
                liveness = self.provider.get_job_liveness(job_id)
                if liveness is not None:
                    if self.provider.is_stale_row(liveness):
                        status = "[bold red]stale[/]"
                    elif liveness.is_terminal():
                        status = f"[dim]{status}[/dim]"
                    cells = [job_id, status] + self.provider.liveness_cells(liveness)
                else:
                    dur_str = f"{j['duration']:.1f}s" if j["duration"] else "—"
                    cells = [job_id, status, "—", "—", dur_str, "—", "—"]
                jobs_table.add_row(*cells, key=job_id)

            # Restore cursor position for jobs
            if self.selected_job and jobs:
                with suppress(RowDoesNotExist):
                    self._programmatic_move = True
                    jobs_table.move_cursor(row=jobs_table.get_row_index(self.selected_job))
                    self._programmatic_move = False
            elif jobs:
                self.selected_job = jobs[0]["job_id"]

        # Update Live Log - show job log if job selected, else show audit log tail
        self._update_live_log(session_id, task_ref, self.selected_job)

    def _update_live_log(self, session_id: Optional[str], task_ref: Optional[str], job_id: Optional[str]):
        """Update the Live Log pane with job log or audit log tail.

        The log pane should never be empty - show audit log tail when no job is selected.
        """
        log_pane = self.query_one("#log-pane", RichLog)
        log_pane.clear()

        if job_id and session_id and task_ref:
            # Show job log if job is selected
            log_text = self.provider.get_job_log(session_id, task_ref, job_id)
            if log_text:
                log_pane.write(_escape(log_text))
            else:
                log_pane.write("[dim]No log data available[/]")
        else:
            # Show recent audit log events as fallback when no job is selected
            try:
                events = self.provider.get_all_events(limit=20)
                if events:
                    for event in reversed(events):  # Show newest first
                        timestamp = event.get("timestamp", "?")
                        event_type = event.get("event_type", "unknown")
                        detail = event.get("detail", "")
                        summary = f"[dim]{timestamp}[/] {event_type}"
                        if detail:
                            summary += f" - {detail[:60]}"
                        log_pane.write(summary)
                else:
                    log_pane.write("[dim]No audit log events[/]")
            except Exception:
                log_pane.write("[dim]Audit log[/]")

    def _update_header(self):
        header = self.query_one("#cockpit-header", Static)
        project = self.provider.project_name
        active_mode = self.provider.get_active_mode()
        active_id = self.provider.get_active_session_id()
        active_short = _short_id(active_id) if active_id else "none"
        header.update(
            f"  [bold]{project}[/] > Cockpit  "
            f"|  Active Mode: [bold green]{active_mode or '—'}[/]  "
            f"|  Active Session: [bold green]{active_short}[/]"
        )
        self.app.sub_title = (
            "  :protocol  :settings  :sessions  |  r:refresh  ::command  q:quit"
        )
