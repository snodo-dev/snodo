"""Linked Cascade Cockpit panel screen for Snodo dashboard.

FILE: snodo/dashboard/panels/cockpit.py
"""

from typing import Any, Dict, Optional, List

from rich.markup import escape as _escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, RichLog, Static

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
        
        # Selection state cache
        self.selected_session: Optional[str] = None
        self.selected_wave: Optional[str] = None
        self.selected_task: Optional[str] = None
        self.selected_job: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="cockpit-header")
        
        with Horizontal(classes="cockpit-row"):
            with Vertical(classes="pane"):
                yield Static("Sessions", classes="pane-title")
                yield DataTable(id="sessions-table", cursor_type="row")
            with Vertical(classes="pane"):
                yield Static("Waves", classes="pane-title")
                yield DataTable(id="waves-table", cursor_type="row")
            with Vertical(classes="pane"):
                yield Static("Tasks Tree", classes="pane-title")
                yield DataTable(id="tasks-table", cursor_type="row")
                
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
        
        waves_table = self.query_one("#waves-table", DataTable)
        waves_table.add_columns("Wave", "Description")
        
        tasks_table = self.query_one("#tasks-table", DataTable)
        tasks_table.add_columns("Task ID", "Status")
        
        jobs_table = self.query_one("#jobs-table", DataTable)
        jobs_table.add_columns("Job ID", "Status", "Duration")

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
        """Standard refresh to query provider and update pane hierarchies."""
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
            try:
                sessions_table.move_cursor(row=sessions_table.get_row_index(self.selected_session))
            except Exception:
                pass
                
        # Update Cockpit Header
        self._update_header()
        
        # Trigger cascade update
        self._cascade_update()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Handle user moving cursor to update cascade state."""
        table_id = event.data_table.id
        row_key = event.row_key
        if not row_key:
            return
        row_id = getattr(row_key, "value", None) or str(row_key)
        
        if table_id == "sessions-table":
            if self.selected_session != row_id:
                self.selected_session = row_id
                self.selected_wave = None
                self.selected_task = None
                self.selected_job = None
                self._cascade_update()
        elif table_id == "waves-table":
            if self.selected_wave != row_id:
                self.selected_wave = row_id
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
        """Update tables in cascade based on selection states."""
        session_id = self.selected_session
        if not session_id:
            self.query_one("#waves-table", DataTable).clear()
            self.query_one("#tasks-table", DataTable).clear()
            self.query_one("#jobs-table", DataTable).clear()
            self.query_one("#log-pane", RichLog).clear()
            return

        # Update Waves
        waves = self.provider.get_waves(session_id)
        waves_table = self.query_one("#waves-table", DataTable)
        waves_table.clear()
        for w in waves:
            waves_table.add_row(w["wave_id"], w["feature_description"], key=w["wave_id"])
            
        if not self.selected_wave and waves:
            self.selected_wave = waves[0]["wave_id"]
            
        if self.selected_wave:
            try:
                waves_table.move_cursor(row=waves_table.get_row_index(self.selected_wave))
            except Exception:
                pass

        # Update Tasks
        wave_id = self.selected_wave
        tasks_table = self.query_one("#tasks-table", DataTable)
        tasks_table.clear()
        
        all_tasks = self.provider.get_tasks(session_id)
        # Filter tasks by wave if wave is selected
        wave_task_ids = set()
        if wave_id:
            for w in waves:
                if w["wave_id"] == wave_id:
                    wave_task_ids = set(w["task_ids"])
                    break
        
        filtered_tasks = [t for t in all_tasks if not wave_id or t["task_id"] in wave_task_ids]
        flat_tasks = _flatten_tasks(filtered_tasks)
        
        for t in flat_tasks:
            indent = "  " * t["depth"] + ("↳ " if t["depth"] > 0 else "")
            display_id = indent + t["task_id"]
            tasks_table.add_row(display_id, t["status"], key=t["task_ref"])
            
        if not self.selected_task and flat_tasks:
            self.selected_task = flat_tasks[0]["task_ref"]
            
        if self.selected_task:
            try:
                tasks_table.move_cursor(row=tasks_table.get_row_index(self.selected_task))
            except Exception:
                pass

        # Update Jobs
        task_ref = self.selected_task
        jobs_table = self.query_one("#jobs-table", DataTable)
        jobs_table.clear()
        
        if not task_ref:
            self.query_one("#log-pane", RichLog).clear()
            return
            
        jobs = self.provider.get_jobs(session_id, task_ref)
        for j in jobs:
            dur_str = f"{j['duration']:.1f}s" if j["duration"] else "—"
            jobs_table.add_row(j["job_id"], j["status"], dur_str, key=j["job_id"])
            
        if not self.selected_job and jobs:
            self.selected_job = jobs[0]["job_id"]
            
        if self.selected_job:
            try:
                jobs_table.move_cursor(row=jobs_table.get_row_index(self.selected_job))
            except Exception:
                pass

        # Update Live Log
        job_id = self.selected_job
        log_pane = self.query_one("#log-pane", RichLog)
        log_pane.clear()
        if job_id:
            log_text = self.provider.get_job_log(session_id, task_ref, job_id)
            log_pane.write(_escape(log_text))

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
