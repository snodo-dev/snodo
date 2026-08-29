"""Dashboard screens — sessions list and session detail.

FILE: snodo/dashboard/screens.py  (Task: dashboard rebuild)
"""

import time as _time
from typing import Any, Dict, Optional

from rich.markup import escape as _escape

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Static


def _relative_time(iso_ts: Optional[str]) -> str:
    """Convert an ISO timestamp to a short relative string."""
    if not iso_ts:
        return "—"
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso_ts)
        now = datetime.now(timezone.utc)
        elapsed = (now - dt).total_seconds()
    except (ValueError, TypeError):
        return iso_ts[:16] if len(iso_ts) >= 16 else iso_ts
    if elapsed < 0:
        return "just now"
    if elapsed < 60:
        return f"{int(elapsed)}s ago"
    if elapsed < 3600:
        return f"{int(elapsed / 60)}m ago"
    if elapsed < 86400:
        return f"{int(elapsed / 3600)}h ago"
    return f"{int(elapsed / 86400)}d ago"


def _short_id(session_id: str) -> str:
    """Compact session ID for display: last 12 chars."""
    return session_id[-12:] if len(session_id) > 14 else session_id


# ---------------------------------------------------------------------------
# SessionsScreen
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# SessionDetailScreen
# ---------------------------------------------------------------------------

class SessionDetailScreen(Screen):
    """Drill-down view for a single session."""

    BINDINGS = [
        Binding("escape", "pop_screen", "Back"),
        Binding("q", "quit", "Quit"),
    ]

    CSS = """
    SessionDetailScreen {
        layout: vertical;
    }
    #detail-header {
        height: auto;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
        border-bottom: solid $primary;
    }
    #detail-body {
        layout: horizontal;
        height: 1fr;
    }
    #detail-left {
        width: 2fr;
        height: 1fr;
    }
    #detail-right {
        width: 1fr;
        height: 1fr;
    }
    #detail-task-history {
        height: auto;
        max-height: 14;
        border: solid $error;
        margin: 0 1;
        padding: 0 1;
    }
    #detail-events {
        height: 1fr;
        border: solid $warning;
        margin: 0 1 1 1;
        padding: 0 1;
    }
    #detail-validators {
        height: 1fr;
        border: solid $secondary;
        margin: 0 1;
        padding: 0 1;
    }
    #detail-agents {
        height: 1fr;
        border: solid $accent;
        margin: 0 1;
        padding: 0 1;
    }
    .section-title {
        text-style: bold;
        color: $text;
    }
    """

    def __init__(self, detail: Any, provider: Any, **kwargs):
        super().__init__(**kwargs)
        self.detail = detail
        self.provider = provider
        self._events_row_keys: Dict[int, Any] = {}
        self._refresh_timer: Any = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="detail-header")
        with Horizontal(id="detail-body"):
            with VerticalScroll(id="detail-left"):
                with Container(id="detail-task-history"):
                    yield Static("TASK HISTORY", classes="section-title")
                    yield DataTable(id="task-history-table", cursor_type="row")
                with Container(id="detail-events"):
                    yield Static("EVENTS", classes="section-title")
                    yield DataTable(id="events-table", cursor_type="row")
            with Vertical(id="detail-right"):
                with Container(id="detail-validators"):
                    yield Static("VALIDATORS", classes="section-title")
                    yield DataTable(id="validators-table", cursor_type="row")
                with Container(id="detail-agents"):
                    yield Static("AGENTS", classes="section-title")
                    yield DataTable(id="agents-table", cursor_type="row")
        yield Footer()

    def on_mount(self):
        self._populate()
        self._update_header()
        self._refresh_timer = self.set_interval(2.0, self._refresh_events)

    def _populate(self):
        d = self.detail

        # Task History
        th = self.query_one("#task-history-table", DataTable)
        th.add_columns("Task", "Validate", "Post-Val", "Outcome")
        for te in d.task_history:
            task_short = _short_id(te["task_ref"]) if te["task_ref"] else "—"
            pre_display = self._format_validator_results(
                te.get("pre_results", []))
            post_display = self._format_validator_results(
                te.get("post_results", []))
            outcome_display = self._format_outcome(te)
            th.add_row(task_short, pre_display, post_display, outcome_display)

        # Validators
        vt = self.query_one("#validators-table", DataTable)
        vt.add_columns("ID", "Type", "Phase", "Severity Cap")
        for v in d.validators:
            cap = v.get("severity_cap") or "—"
            vt.add_row(v["validator_id"], v.get("validator_type", "—"),
                       v.get("evaluation_phase", "—"), cap)

        # Agents
        at = self.query_one("#agents-table", DataTable)
        at.add_columns("Agent", "Thread", "Tasks", "Last Used")
        for a in d.agents:
            thread = a.get("thread_id", "")[:8]
            tasks = str(a.get("task_count", 0))
            last = _relative_time_last_ts(a.get("last_used_at"))
            at.add_row(a["id"], thread, tasks, last)

        # Events
        et = self.query_one("#events-table", DataTable)
        et.add_columns("Time", "Type", "Summary")
        self._events_row_keys.clear()
        for ev in d.events[-50:]:
            cells = self._event_cells(ev)
            self._events_row_keys[ev.sequence] = et.add_row(*cells, key=str(ev.sequence))

    @staticmethod
    def _format_validator_results(results: list) -> str:
        if not results:
            return "—"
        parts = []
        for r in results[:4]:
            vid = _escape(str(r.get("validator_id", "?")))
            sev = r.get("severity", "?")
            if sev == "blocker":
                parts.append(f"[red]{vid}:✗[/]")
            elif sev == "warn":
                parts.append(f"[yellow]{vid}:![/]")
            else:
                parts.append(f"[green]{vid}:✓[/]")
        if len(results) > 4:
            parts.append("…")
        return " ".join(parts)

    @staticmethod
    def _format_outcome(te: dict) -> str:
        color = te.get("outcome_color", "")
        text = _escape(te["outcome"])
        if color:
            return f"[{color}]{text}[/]"
        return text

    @staticmethod
    def _event_cells(ev: Any) -> list:
        """Build escaped cell values for an audit event."""
        ts = ev.timestamp[11:19] if len(ev.timestamp) >= 19 else ev.timestamp
        ev_type = _escape(ev.event_type)
        color = _event_color(ev.event_type)
        type_cell = f"[{color}]{ev_type}[/]" if color else ev_type
        summary_cell = _escape(_summarize_event(ev))
        return [ts, type_cell, summary_cell]

    def _refresh_events(self):
        """Tail the audit log and update the events table in-place."""
        detail = self.provider.get_session_detail(self.detail.session_id)
        if detail is None:
            return
        self.detail = detail
        table = self.query_one("#events-table", DataTable)
        events = detail.events[-50:]

        new_ids = {ev.sequence: ev for ev in events}

        gone = [seq for seq in self._events_row_keys if seq not in new_ids]
        for seq in gone:
            if seq in self._events_row_keys:
                table.remove_row(self._events_row_keys[seq])
                del self._events_row_keys[seq]

        for seq, ev in new_ids.items():
            cells = self._event_cells(ev)
            if seq in self._events_row_keys:
                row_key = self._events_row_keys[seq]
                existing = table.get_row(row_key)
                for col_idx, new_val in enumerate(cells):
                    if col_idx < len(existing) and existing[col_idx] != new_val:
                        table.update_cell_at(Coordinate(table.get_row_index(row_key), col_idx), new_val)
            else:
                self._events_row_keys[seq] = table.add_row(*cells, key=str(seq))

    def _update_header(self):
        header = self.query_one("#detail-header", Static)
        d = self.detail
        active_id = self.provider.get_active_session_id()
        short = _short_id(d.session_id)
        is_active = d.session_id == active_id
        active_tag = " [bold green](active)[/]" if is_active else ""
        status = ""
        if d.is_escalated:
            status = " [bold red]ESCALATED[/]"
        elif d.is_halted:
            status = " [bold red]HALTED[/]"
        header.update(
            f"  [bold]{self.provider.project_name}[/] > sessions > "
            f"{short}{active_tag}{status}  "
            f"|  mode: [bold]{d.mode_id}[/]  "
            f"|  task: {d.current_task or '—'}"
        )


# ---------------------------------------------------------------------------
# EventsScreen
# ---------------------------------------------------------------------------

class EventsScreen(Screen):
    """Full-screen audit events view."""

    BINDINGS = [
        Binding("escape", "pop_screen", "Back"),
        Binding("q", "quit", "Quit"),
        Binding("/", "filter_events", "Filter"),
    ]

    CSS = """
    EventsScreen {
        layout: vertical;
    }
    #events-header {
        height: auto;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
        border-bottom: solid $primary;
    }
    #events-table {
        height: 1fr;
    }
    #events-filter {
        height: 1;
        dock: bottom;
        visibility: hidden;
        border-top: solid $secondary;
    }
    #events-filter:focus-within {
        visibility: visible;
    }
    """

    def __init__(self, provider: Any, **kwargs):
        super().__init__(**kwargs)
        self.provider = provider
        self._event_row_keys: Dict[int, Any] = {}
        self._all_events: list = []
        self._events_filter_text: str = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="events-header")
        yield DataTable(id="events-table", cursor_type="row")
        yield Input(id="events-filter", placeholder="/filter events...")
        yield Footer()

    def on_mount(self):
        header = self.query_one("#events-header", Static)
        header.update(f"  [bold]{self.provider.project_name}[/] > events")
        et = self.query_one("#events-table", DataTable)
        et.add_columns("Seq", "Time", "Type", "Summary")
        events = self.provider.get_all_events()
        self._all_events = events
        for ev in events[-80:]:
            key = str(ev.sequence)
            color = _event_color(ev.event_type)
            ev_type = _escape(ev.event_type)
            type_cell = f"[{color}]{ev_type}[/]" if color else ev_type
            rk = et.add_row(
                str(ev.sequence),
                ev.timestamp[11:19] if len(ev.timestamp) >= 19 else ev.timestamp,
                type_cell,
                _escape(_summarize_event(ev)),
                key=key,
            )
            self._event_row_keys[ev.sequence] = rk

    def action_filter_events(self):
        fb = self.query_one("#events-filter", Input)
        fb.visible = True
        fb.focus()

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "events-filter":
            self._apply_events_filter(event.value.strip())
            event.input.value = ""
            event.input.visible = False
            self.query_one("#events-table", DataTable).focus()

    def _apply_events_filter(self, text: str):
        table = self.query_one("#events-table", DataTable)
        self._events_filter_text = text

        visible = self._all_events[-80:]
        if text:
            visible = [ev for ev in visible if self._event_matches(ev, text.lower())]

        new_ids: Dict[int, Any] = {ev.sequence: ev for ev in visible}

        gone = [seq for seq in self._event_row_keys if seq not in new_ids]
        for seq in gone:
            if seq in self._event_row_keys:
                table.remove_row(self._event_row_keys[seq])
                del self._event_row_keys[seq]

        for seq, ev in new_ids.items():
            ts = ev.timestamp[11:19] if len(ev.timestamp) >= 19 else ev.timestamp
            color = _event_color(ev.event_type)
            ev_type = _escape(ev.event_type)
            type_cell = f"[{color}]{ev_type}[/]" if color else ev_type
            cells = [str(ev.sequence), ts, type_cell,
                     _escape(_summarize_event(ev))]

            if seq in self._event_row_keys:
                row_key = self._event_row_keys[seq]
                existing = table.get_row(row_key)
                for col_idx, new_val in enumerate(cells):
                    if col_idx < len(existing) and existing[col_idx] != new_val:
                        table.update_cell_at(Coordinate(table.get_row_index(row_key), col_idx), new_val)
            else:
                self._event_row_keys[seq] = table.add_row(*cells, key=str(seq))

    @staticmethod
    def _event_matches(ev: Any, text: str) -> bool:
        summary = _summarize_event(ev).lower()
        if text in summary:
            return True
        if text in ev.event_type.lower():
            return True
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _relative_time_last_ts(ts) -> str:
    if not ts:
        return "never"
    elapsed = _time.time() - ts
    if elapsed < 60:
        return "just now"
    if elapsed < 3600:
        return f"{int(elapsed / 60)}m ago"
    if elapsed < 86400:
        return f"{int(elapsed / 3600)}h ago"
    return f"{int(elapsed / 86400)}d ago"


def _summarize_event(ev: Any) -> str:
    data = ev.data if isinstance(ev.data, dict) else {}
    op = data.get("op", ev.event_type)
    if op == "validate":
        outcome = data.get("outcome", "?")
        validators = ", ".join(data.get("validators_invoked", [])[:3])
        return f"{outcome}  [{validators}]"
    if op == "dispatch":
        task = data.get("task_ref", "")
        return f"task={_short_id(task)}" if task else ""
    if op == "halt":
        reason = data.get("reason", "")
        return reason[:60]
    if op == "disagreement_escalated":
        task = data.get("task_ref", "")
        return f"task={_short_id(task)}" if task else ""
    if op == "session_started":
        return f"mode={data.get('mode', '')}"
    if op == "task_complete":
        return f"task={_short_id(data.get('task_ref', ''))}"
    if op == "disagreement_resolved":
        return f"{data.get('resolution', '')}  task={_short_id(data.get('task_ref', ''))}"
    # Generic: show first key-value pair
    for k, v in data.items():
        if k == "op":
            continue
        sv = str(v)[:40]
        return f"{k}={sv}"
    return ""


def _event_color(event_type: str) -> str:
    if event_type in ("halt", "blocker"):
        return "red"
    if event_type == "disagreement_escalated":
        return "bold red"
    if event_type == "validate":
        return "yellow"
    if event_type == "dispatch":
        return "green"
    if event_type == "task_complete":
        return "bold green"
    if event_type == "disagreement_resolved":
        return "blue"
    if event_type == "session_started":
        return "cyan"
    return ""
