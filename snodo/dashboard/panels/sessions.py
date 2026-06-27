"""Sessions panel screen for Snodo dashboard.

FILE: snodo/dashboard/panels/sessions.py
"""

from typing import Any, Dict, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Static

from snodo.dashboard.panels import register_panel, get_panel
from snodo.dashboard.screens import _relative_time, _short_id, SessionDetailScreen


@register_panel("sessions")
class SessionsScreen(Screen):
    """Primary view: live sessions resource table (k9s-style)."""

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding(":", "command_mode", "Commands"),
        Binding("/", "filter_mode", "Filter"),
        Binding("escape", "clear_filter", "Clear", show=False),
    ]

    CSS = """
    SessionsScreen {
        layout: vertical;
    }
    #session-header {
        height: auto;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
        border-bottom: solid $primary;
    }
    #session-header .workspace {
        color: $text;
    }
    #session-header .separator {
        color: $primary;
    }
    #session-table {
        height: 1fr;
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
    #filter-bar {
        height: 1;
        dock: bottom;
        visibility: hidden;
        border-top: solid $secondary;
    }
    #filter-bar:focus-within {
        visibility: visible;
    }
    """

    def __init__(self, provider: Any, **kwargs):
        super().__init__(**kwargs)
        self.provider = provider
        self._row_keys: Dict[str, Any] = {}
        self._refresh_timer: Any = None
        self._all_sessions: list = []
        self._filter_text: str = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="session-header")
        yield DataTable(id="session-table", cursor_type="row")
        yield Input(id="command-bar", placeholder=":command  (e.g. :sessions, :events)")
        yield Input(id="filter-bar", placeholder="/filter...")
        yield Footer()

    def on_mount(self):
        table = self.query_one("#session-table", DataTable)
        table.add_columns("Session", "Mode", "#A", "#V", "Last Event", "Status")
        self._refresh()
        self._refresh_timer = self.set_interval(2.0, self._refresh)
        self._update_header()

    def on_screen_resume(self):
        self._refresh()
        self._update_header()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_refresh(self):
        self._refresh()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter on a session row → drill into session detail."""
        if event.row_key is None:
            return
        session_id = self._row_key_to_session_id(event.row_key)
        if session_id is None:
            return
        detail = self.provider.get_session_detail(session_id)
        if detail is None:
            self.notify(f"Session {session_id} not found", severity="error")
            return
        self.app.push_screen(SessionDetailScreen(detail, self.provider))

    def action_command_mode(self):
        cmd = self.query_one("#command-bar", Input)
        cmd.visible = True
        cmd.focus()

    def action_filter_mode(self):
        fb = self.query_one("#filter-bar", Input)
        fb.visible = True
        fb.focus()

    def action_clear_filter(self):
        fb = self.query_one("#filter-bar", Input)
        fb.value = ""
        fb.visible = False
        self._filter_text = ""
        self.query_one("#session-table", DataTable).focus()
        self._rebuild_rows()

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "command-bar":
            raw = event.value.strip()
            event.input.value = ""
            event.input.visible = False
            self.query_one("#session-table", DataTable).focus()
            if raw.startswith(":"):
                cmd = raw[1:].strip().lower()
                self._handle_command(cmd)

    def _handle_command(self, cmd: str):
        known = {"cockpit", "protocol", "settings"}
        if cmd in known:
            self.app.push_screen(get_panel(cmd, self.provider))
        elif cmd == "sessions":
            self.notify("Already in sessions view")
        else:
            self.notify(f"Unknown command: :{cmd}", severity="error")

    def _match_filter(self, s: Any) -> bool:
        if not self._filter_text:
            return True
        text = self._filter_text.lower()
        keywords = [s.session_id.lower(), s.mode.lower(),
                    (s.last_event_type or "").lower(), (s.current_task or "").lower()]
        return any(text in kw for kw in keywords)

    def _apply_filter(self, text: str):
        self._filter_text = text
        self._rebuild_rows()

    def _refresh(self):
        """In-place refresh: update existing rows, add new, remove gone.

        Never calls table.clear() — preserves cursor position.
        """
        sessions = self.provider.get_sessions()
        table = self.query_one("#session-table", DataTable)

        self._all_sessions = sessions

        if self._filter_text:
            sessions = [s for s in sessions if self._match_filter(s)]

        new_ids: Dict[str, Any] = {}
        for s in sessions:
            new_ids[s.session_id] = s

        gone = [sid for sid in self._row_keys if sid not in new_ids]
        for sid in gone:
            if sid in self._row_keys:
                table.remove_row(self._row_keys[sid])
                del self._row_keys[sid]

        for sid, s in new_ids.items():
            cells = self._build_row_cells(sid, s)
            self._sync_row(table, sid, cells)

        self._update_header()
        self._update_status_footer(table)

    def _rebuild_rows(self):
        """Apply filter to existing cached sessions — in-place, no clear()."""
        table = self.query_one("#session-table", DataTable)
        sessions = self._all_sessions
        if self._filter_text:
            sessions = [s for s in sessions if self._match_filter(s)]

        new_ids: Dict[str, Any] = {}
        for s in sessions:
            new_ids[s.session_id] = s

        gone = [sid for sid in self._row_keys if sid not in new_ids]
        for sid in gone:
            if sid in self._row_keys:
                table.remove_row(self._row_keys[sid])
                del self._row_keys[sid]

        for sid, s in new_ids.items():
            cells = self._build_row_cells(sid, s)
            self._sync_row(table, sid, cells)

        self._update_header()
        self._update_status_footer(table)

    def _build_row_cells(self, sid: str, s: Any) -> list:
        """Build cell values for a session row."""
        short = _short_id(sid)
        status_str = self._status_cell(s)
        last_event = _relative_time(s.last_event_at) if s.last_event_at else "—"
        return [short, s.mode, str(s.agent_count),
                str(s.validator_count), last_event, status_str]

    def _sync_row(self, table: DataTable, sid: str, cells: list) -> None:
        """Update or add a row by session_id (used as the RowKey)."""
        if sid in self._row_keys:
            row_key = self._row_keys[sid]
            existing = table.get_row(row_key)
            for col_idx, new_val in enumerate(cells):
                if col_idx < len(existing) and existing[col_idx] != new_val:
                    table.update_cell_at(Coordinate(table.get_row_index(row_key), col_idx), new_val)
        else:
            self._row_keys[sid] = table.add_row(*cells, key=sid)

    def _status_cell(self, s: Any) -> str:
        if s.is_escalated:
            return "[bold red]esc[/]"
        if s.is_halted:
            return "[bold red]halted[/]"
        if s.is_active:
            return "[bold green]active[/]"
        return "—"

    def _update_header(self):
        header = self.query_one("#session-header", Static)
        project = self.provider.project_name
        active_mode = self.provider.get_active_mode()
        active_id = self.provider.get_active_session_id()
        active_short = _short_id(active_id) if active_id else "none"
        sessions = self.provider.get_sessions()
        header.update(
            f"  [bold]{project}[/] > sessions  "
            f"|  active: [bold green]{active_short}[/] ({active_mode or '—'})  "
            f"|  sessions: {len(sessions)}"
        )

    def _update_status_footer(self, table: DataTable):
        rows = table.row_count
        sel = (table.cursor_row or 0) + 1 if table.row_count else 0
        self.app.sub_title = f"Row {sel}/{rows}  |  Enter:detail  /:filter  ::commands  q:quit"

    def _row_key_to_session_id(self, row_key: Any) -> Optional[str]:
        """Resolve session_id from a RowKey (RowKey.value == session_id)."""
        value = getattr(row_key, "value", None) or str(row_key)
        if value in self._row_keys:
            return value
        return None
