"""Linked Cascade Cockpit panel screen for Snodo dashboard.

FILE: snodo/dashboard/panels/cockpit.py

The cockpit is an observer: it reads and displays, and it acts nowhere. Every
action that changes state (dispatch, plan, recon, authorize) belongs to
``snodo cloud``/``snodo``, where it is attributable to an identity. So the
panes here answer only "what do I need to look at, and why" — and the one pane
that earns the most space is the one that names what will not move until a
person acts.

Layout:
    row 1: Needs You (awaiting a human) | Tasks Tree
    row 2: Jobs                          | Live Log

The Sessions pane used to occupy the upper-left. It earned that space only when
there were many sessions, which is not the normal case; its single fact — which
session is active — is already printed in the header. It is gone; the space now
shows what requires a human, halts grouped by their recorded outcome, and the
project cost the per-call usage records already carry.
"""

from contextlib import suppress
import time
from typing import Any, Dict, List, Optional

from rich.ansi import AnsiDecoder
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, RichLog, Static
from textual.widgets.data_table import RowDoesNotExist

from snodo.dashboard.liveness import fmt_age
from snodo.dashboard.panels import register_panel, get_panel
from snodo.dashboard.screens import _short_id


def _ansi_to_text(content: str) -> Text:
    """Decode ANSI escape sequences in job output into a styled Text.

    Job stdout is terminal output, not Rich markup. ``AnsiDecoder`` turns the
    escape sequences into styled text so colours render instead of printing
    ``[1;31m`` literally; writing the result as a ``Text`` keeps it out of
    Rich's square-bracket markup parser entirely.
    """
    text = Text()
    for i, part in enumerate(AnsiDecoder().decode(content)):
        if i:
            text.append("\n")
        text.append_text(part)
    return text


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
    """Cockpit view: needs-you | tasks top row, jobs | logs bottom row."""

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("/", "search_mode", "Search"),
        Binding("n", "search_next", "Next"),
        Binding(":", "command_mode", "Command"),
        Binding("w", "open_wave", "Wave"),
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
    #search-bar {
        height: 1;
        dock: bottom;
        visibility: hidden;
        border-top: solid $secondary;
    }
    #search-bar:focus-within {
        visibility: visible;
    }
    """

    def __init__(self, provider: Any, **kwargs):
        super().__init__(**kwargs)
        self.provider = provider
        self._programmatic_move = False  # Flag to distinguish refresh moves from keypresses
        self._data_read_time: Optional[float] = None  # Timestamp of last data read

        # Selection state cache
        self.selected_session: Optional[str] = None
        self.selected_task: Optional[str] = None
        self.selected_job: Optional[str] = None

        # Session the settled Tasks/Jobs panes were last read for. They are
        # settled records: not re-read as the operator moves inside them.
        self._tasks_built_for: Optional[str] = None

        # Search state: the last query and the current position in its hits,
        # so pressing 'n' cycles matches without a second disk read.
        self._search_query: str = ""
        self._search_hits: List[Dict[str, Any]] = []
        self._search_index: int = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="cockpit-header")

        # Top row: Needs You | Tasks Tree. The Sessions pane is gone: its one
        # fact lives in the header, and the operator's attention belongs on
        # what will not move without a person.
        with Horizontal(classes="cockpit-row"):
            with Vertical(classes="pane"):
                yield Static("Needs You", classes="pane-title")
                yield RichLog(id="attention-pane", highlight=False, markup=True)
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

        yield Input(id="search-bar", placeholder="/  find a task, job, or audit event (n: next)  — output not searched")
        yield Input(id="command-bar", placeholder=":command  (e.g. :protocol, :settings, :sessions)")
        yield Footer()

    def on_mount(self):
        # Configure columns
        tasks_table = self.query_one("#tasks-table", DataTable)
        tasks_table.add_columns("Task ID", "Wave", "Status", "Phase", "Phase For", "Idle", "Alive?", "Cost")

        jobs_table = self.query_one("#jobs-table", DataTable)
        jobs_table.add_columns("Job ID", "Task", "Status", "Phase", "Phase For", "Idle", "Alive?", "Cost")

        # The initial read is performed once, in on_screen_resume, which fires
        # as this screen becomes active — doing it here too would read twice on
        # mount. Refresh stays explicit thereafter (the 'r' key); there is no
        # timer.

    def on_screen_resume(self):
        # The single read for this activation: on first show and on every
        # return to the screen. Exactly one read pass per activation.
        self._refresh()

    def action_refresh(self):
        self._refresh()

    def action_command_mode(self):
        cmd = self.query_one("#command-bar", Input)
        cmd.visible = True
        cmd.focus()

    def action_search_mode(self):
        search = self.query_one("#search-bar", Input)
        search.visible = True
        search.focus()

    def action_search_next(self):
        if self._search_hits:
            self._search_index = (self._search_index + 1) % len(self._search_hits)
            self._go_to_hit(self._search_hits[self._search_index])

    def action_open_wave(self) -> None:
        """Open the wave that owns the selected task.

        A drill-down, not a navigation level: the cockpit keeps its selection
        and is restored to it when the wave view closes.
        """
        if not self.selected_session or not self.selected_task:
            self.notify("No task selected", severity="warning")
            return

        wave_id = None
        for wave in self.provider.get_waves(self.selected_session):
            if self.selected_task in wave.get("task_ids", []):
                wave_id = wave.get("wave_id")
                break

        if not wave_id:
            self.notify("Task does not belong to a wave", severity="information")
            return

        wave_data = self.provider.get_wave_detail(wave_id)
        if not wave_data:
            self.notify("Wave not found", severity="warning")
            return

        from snodo.dashboard.screens import WaveDetailScreen

        flat = _flatten_tasks(self.provider.get_tasks(self.selected_session))
        self.app.push_screen(
            WaveDetailScreen(wave_data, {t["task_id"]: t for t in flat})
        )

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "command-bar":
            raw = event.value.strip()
            event.input.value = ""
            event.input.visible = False
            self._focus_default()
            if raw.startswith(":"):
                cmd = raw[1:].strip().lower()
                self._handle_command(cmd)
        elif event.input.id == "search-bar":
            query = event.value
            event.input.value = ""
            event.input.visible = False
            self._run_search(query)

    def _focus_default(self):
        with suppress(Exception):
            self.query_one("#tasks-table", DataTable).focus()

    def _handle_command(self, cmd: str):
        known = {"sessions", "protocol", "settings"}
        if cmd in known:
            self.app.push_screen(get_panel(cmd, self.provider))
        elif cmd in ("cockpit", "dashboard"):
            self.notify("Already in cockpit view")
        elif cmd.startswith("output "):
            # Opt-in, explicitly slower: searching job output is the one read
            # that touches a job's stdout/stderr, so it is never the default.
            self._run_output_search(cmd[len("output "):].strip())
        else:
            self.notify(f"Unknown command: :{cmd}", severity="error")

    def _run_output_search(self, query: str) -> None:
        if not query:
            return
        self.notify("Searching job output — this reads each job's log tail and is slower",
                    severity="warning", timeout=8)
        hits = self.provider.search_job_output(query)
        if not hits:
            self.notify(f"No job output match for {query!r}", severity="warning")
            return
        self._search_query = query
        self._search_hits = hits
        self._search_index = 0
        self._go_to_hit(hits[0])
        self.notify(f"{query!r}: found in {len(hits)} job(s) — 1st is a {hits[0]['kind']}  (n: next)")

    def _refresh(self):
        """One read pass: query the provider and update every pane.

        Uses _programmatic_move flag to prevent RowHighlighted events from
        being mistaken for user keypresses when tables are cleared and rebuilt.
        Exactly one read happens here (collect_snapshot, cached across the
        pass); the screen never re-reads on cursor movement and has no timer.
        """
        # Mark this as a programmatic update (not user input)
        self._programmatic_move = True

        try:
            # Invalidate first: a refresh is one fresh read, not a cache hit.
            self.provider.invalidate_read_caches()

            # The Sessions pane is gone. The cockpit works against the active
            # session; browsing all sessions is :sessions' job. This read is a
            # lock-free state/session-file read, not the full audit parse the
            # old Sessions table performed every refresh.
            brief = self.provider.get_session_brief()
            self.selected_session = brief.get("active_id")

            # Header carries the active session (where the Sessions pane lived).
            self._update_header(brief)

            # Render the "Needs You" pane from the bounded audit tail.
            self._render_attention()

            # Settled records are re-read on an explicit refresh (and when the
            # session changes), never on every cursor move inside them.
            self._tasks_built_for = None

            # Trigger cascade update (which will also use _programmatic_move)
            self._cascade_update()

            # Record when this data was read
            self._data_read_time = time.time()
        finally:
            # Always clear the flag when done, even if an exception occurred
            self._programmatic_move = False

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Handle user moving cursor to update cascade state.

        Ignores programmatic cursor moves during refresh to avoid resetting
        selection when tables are cleared and rebuilt (#239). Only the Live Log
        re-reads on a cursor move — the Tasks and Jobs panes are settled
        records and are rebuilt only when the selected session changes.
        """
        # Ignore programmatic cursor moves during refresh
        if self._programmatic_move:
            return

        table_id = event.data_table.id
        row_key = event.row_key
        if not row_key:
            return
        row_id = getattr(row_key, "value", None) or str(row_key)

        if table_id == "tasks-table":
            if self.selected_task != row_id:
                self.selected_task = row_id
                self.selected_job = None
                # Jobs are listed project-wide; only the Live Log re-reads.
                self._update_live_log(self.selected_session, row_id, None)
        elif table_id == "jobs-table":
            if self.selected_job != row_id:
                self.selected_job = row_id
                self._update_live_log(
                    self.selected_session, self.selected_task, row_id
                )

    def _cascade_update(self):
        """Update tables in cascade based on selection states.

        Hierarchy: Session → Tasks (with wave shown as a column) → Jobs → Live Log.
        Waves are no longer a filter level; all tasks from a session are shown.

        The Tasks and Jobs panes are settled records: they are read from disk
        only when the selected session changes (or an explicit refresh clears
        the cache), never on every cursor move inside them. Moving inside a
        settled pane re-reads nothing but the Live Log.
        """
        session_id = self.selected_session
        if not session_id:
            self._tasks_built_for = None
            self.query_one("#tasks-table", DataTable).clear()
            self.query_one("#jobs-table", DataTable).clear()
            self._update_live_log(None, None, None)
            return

        if self._tasks_built_for != session_id:
            self._tasks_built_for = session_id
            self._populate_tasks(session_id)
            self._populate_jobs()

        self._update_live_log(session_id, self.selected_task, self.selected_job)

    def _populate_tasks(self, session_id: str) -> None:
        """Read and render the Tasks pane for the selected session."""
        # Build a wave_id lookup map: task_ref → wave_id
        waves = self.provider.get_waves(session_id)
        task_to_wave: Dict[str, str] = {}
        for w in waves:
            for task_id in w.get("task_ids", []):
                # Store mapping for both task_id and task_ref (which might be same)
                task_to_wave[task_id] = w["wave_id"]

        tasks_table = self.query_one("#tasks-table", DataTable)
        self._programmatic_move = True
        try:
            tasks_table.clear()
        finally:
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
                try:
                    tasks_table.move_cursor(row=tasks_table.get_row_index(self.selected_task))
                finally:
                    self._programmatic_move = False
        elif flat_tasks:
            self.selected_task = flat_tasks[0]["task_ref"]

    def _populate_jobs(self) -> None:
        """Read and render the Jobs pane: every job, with its task as a column."""
        jobs_table = self.query_one("#jobs-table", DataTable)
        self._programmatic_move = True
        try:
            jobs_table.clear()
        finally:
            self._programmatic_move = False

        jobs = self.provider.get_jobs(self.selected_session)
        for j in jobs:
            job_id = j["job_id"]
            task_ref = j.get("task_ref", "") or "—"
            status = j["status"]
            liveness = self.provider.get_job_liveness(job_id)
            if liveness is not None:
                if self.provider.is_stale_row(liveness):
                    status = "[bold red]stale[/]"
                elif liveness.is_terminal():
                    status = f"[dim]{status}[/dim]"
                cells = [job_id, task_ref, status] + self.provider.liveness_cells(liveness)
            else:
                dur_str = f"{j['duration']:.1f}s" if j["duration"] else "—"
                cells = [job_id, task_ref, status, "—", "—", dur_str, "—", "—"]
            jobs_table.add_row(*cells, key=job_id)

        # Restore cursor position for jobs
        if self.selected_job and jobs:
            with suppress(RowDoesNotExist):
                self._programmatic_move = True
                try:
                    jobs_table.move_cursor(row=jobs_table.get_row_index(self.selected_job))
                finally:
                    self._programmatic_move = False
        elif jobs:
            self.selected_job = jobs[0]["job_id"]

    def _render_attention(self) -> None:
        """Show what will not move until a person acts — or say nothing does.

        The pane names each task awaiting a human, what it is waiting on, and
        how long it has waited. Halts are also grouped by their recorded
        outcome, so a wave that failed the same way six times reads as one
        fact. A project cost rollup uses the per-call usage records already on
        disk. When nothing is waiting the pane says so plainly rather than
        leaving an empty frame.
        """
        pane = self.query_one("#attention-pane", RichLog)
        pane.clear()
        att = self.provider.get_attention()
        awaiting = att.get("awaiting") or []

        if awaiting:
            pane.write(f"[bold red]▼ {len(awaiting)} task(s) waiting on a person[/]")
            for a in awaiting:
                pane.write(f"  [bold]{a['task_ref']}[/]")
                pane.write(
                    f"     awaits: [yellow]{a['awaits']}[/]  ·  "
                    f"waiting: [bold red]{fmt_age(a['waited_seconds'])}[/]"
                )
        else:
            pane.write("[dim]Nothing is waiting on you.[/]")

        pane.write("")
        outcomes = att.get("halt_outcomes") or {}
        if outcomes:
            pane.write("[bold]Halts by outcome[/]")
            for outcome, count in sorted(outcomes.items(), key=lambda kv: (-kv[1], kv[0])):
                plural = "" if count == 1 else "s"
                pane.write(f"  {outcome}: [bold]{count}[/] task{plural}")

        pane.write("")
        cost = att.get("cost") or {}
        total = cost.get("total")
        if total and cost.get("runs_with_cost"):
            prefix = "~" if cost.get("partial") else ""
            pane.write(
                f"[bold]Cost[/]  {prefix}${total:.4f}  "
                f"[dim]across {cost['runs_with_cost']} run(s)[/]"
            )
        else:
            pane.write("[bold]Cost[/]  [dim]no usage recorded[/]")

    def _update_live_log(self, session_id: Optional[str], task_ref: Optional[str], job_id: Optional[str]):
        """Update the Live Log pane with job log or audit log tail.

        The log pane should never be empty - show audit log tail when no job is selected.
        """
        log_pane = self.query_one("#log-pane", RichLog)
        log_pane.clear()

        if job_id and session_id:
            # Show job log if job is selected — a bounded tail, rendered as ANSI.
            # A job's log is keyed by job_id alone; the task is a column, not a
            # precondition, so a job can be read with no task selected.
            log_text = self.provider.get_job_log(session_id, task_ref, job_id)
            if log_text:
                log_pane.write(_ansi_to_text(log_text))
            else:
                log_pane.write("[dim]No log data available[/]")
        else:
            # Show recent audit log events as fallback when no job is selected.
            # Served from the bounded tail the refresh already read — a cursor
            # move here re-renders, it never re-parses the log.
            events = self.provider.get_tail_events(limit=20)
            if events:
                for event in reversed(events):  # Show newest first
                    data = event.get("data") if isinstance(event.get("data"), dict) else {}
                    detail = str(data.get("detail", ""))
                    summary = f"[dim]{event.get('timestamp', '')}[/] {event.get('event_type', '')}"
                    if detail:
                        summary += f" - {detail[:60]}"
                    log_pane.write(summary)
            else:
                log_pane.write("[dim]No audit log events[/]")

    # ------------------------------------------------------------------
    # Search: one place to find a task, a job, or an audit event
    # ------------------------------------------------------------------

    def _run_search(self, query: str) -> None:
        """Find a match and move the operator to it. No output is searched."""
        query = query.strip()
        if not query:
            return
        hits = self.provider.search(query)
        if not hits:
            self._search_hits = []
            self._search_query = ""
            self._search_index = 0
            self.notify(f"No match for {query!r}", severity="warning")
            return
        self._search_query = query
        self._search_hits = hits
        self._search_index = 0
        self._go_to_hit(hits[0])
        self.notify(f"{query!r}: {len(hits)} match(es) — 1st is a {hits[0]['kind']}  (n: next)")

    def _go_to_hit(self, hit: Dict[str, Any]) -> None:
        kind = hit.get("kind")
        key = hit.get("key")
        if kind == "task":
            self._select_in_table("#tasks-table", key)
            self.selected_task = key
            self.selected_job = None
            self._update_live_log(self.selected_session, key, None)
        elif kind == "job":
            self._select_in_table("#jobs-table", key)
            self.selected_job = key
            self._update_live_log(self.selected_session, self.selected_task, key)
        elif kind == "audit":
            self._show_audit_hit(hit.get("event") or {})

    def _select_in_table(self, selector: str, key: Optional[str]) -> None:
        if not key:
            return
        table = self.query_one(selector, DataTable)
        self._programmatic_move = True
        try:
            with suppress(RowDoesNotExist):
                table.move_cursor(row=table.get_row_index(key))
        finally:
            self._programmatic_move = False
        table.focus()

    def _show_audit_hit(self, event: Dict[str, Any]) -> None:
        """Bring an audit event into view: the operator asked to be taken to it."""
        pane = self.query_one("#log-pane", RichLog)
        pane.clear()
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        pane.write(f"[bold]{event.get('event_type', '')}[/]  seq={event.get('sequence', '—')}")
        pane.write(f"[dim]{event.get('timestamp', '')}[/]")
        for k, v in data.items():
            pane.write(f"  {k}: {v}")
        pane.focus()

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def _update_header(self, brief: Optional[Dict[str, Any]] = None):
        header = self.query_one("#cockpit-header", Static)
        project = self.provider.project_name
        if brief is None:
            brief = self.provider.get_session_brief()
        active_id = brief.get("active_id")
        active_mode = brief.get("mode") or self.provider.get_active_mode()
        active_short = _short_id(active_id) if active_id else "none"
        session_count = brief.get("count", 0)

        # Show data age: how long ago this was read
        age_str = "—"
        if self._data_read_time:
            age_secs = time.time() - self._data_read_time
            if age_secs < 60:
                age_str = f"{int(age_secs)}s"
            elif age_secs < 3600:
                age_str = f"{int(age_secs / 60)}m"
            else:
                age_str = f"{int(age_secs / 3600)}h"

        header.update(
            f"  [bold]{project}[/] > Cockpit  "
            f"|  Active Mode: [bold green]{active_mode or '—'}[/]  "
            f"|  Active Session: [bold green]{active_short}[/]  "
            f"|  Sessions: {session_count}  "
            f"|  Data: [dim]{age_str} ago[/]"
        )
        self.app.sub_title = (
            "  /:search  n:next  :protocol  :settings  :sessions  |  r:refresh  q:quit"
        )
